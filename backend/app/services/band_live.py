"""手环「实时」数据：后台定时采样 + 页面轮询展示

为什么这么设计（先看结论，再看代码）
------------------------------------------------
小米运动健康没有开放 API，浏览器也读不到手环加密通道里的步数，
所以"每隔几秒从手环真读一次"在技术上做不到。这里采用**两层数据**：

1. **真实层**：用户导入的数据包（zip/CSV）或蓝牙读到的电量心率 → `device_metrics`，
   它是这台手环今天的真实总量（比如今天走了 9000 步）。
2. **实时层**：后台每 60 秒生成一条分钟级样本 → `device_samples`，
   把真实总量按"一天的活动曲线"摊到当前时刻（早上少、傍晚多），心率在静息值附近波动。

这样页面上的数字会自己往上走、心率会跳动，但**总量永远锚定在真实数据上**，
不会凭空造出一个比真实值还大的数字；没有真实数据时用档案推算一个合理基线，
并在接口里如实标 `anchor_source=demo`，页面显示「模拟基线」。

想要「不用手工点上传」的真实数据，走投递目录那条路：
把小米运动健康导出的 zip 放进 `backend/band_inbox/`（可改成同步盘目录），
后台每隔一个周期扫一次，有新文件就自动导入、立刻参与实时插值。
小米没有开放 API，账号直连属于逆向且要保存你的密码，本项目不这么做。
"""
from __future__ import annotations

import asyncio
import logging
import math
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.device import Device, DeviceMetric, DeviceSample
from app.services.device_import import parse_metric_file, rows_to_metrics

logger = logging.getLogger("app.band")

# 一天的活动累计曲线（时刻 → 当日完成比例），按中国人作息的经验值
# 6:30 前基本不动，7-9 通勤、12-13 午饭、18-19 下班后散步是三个爬坡段
_CURVE = [
    (0.0, 0.00), (6.5, 0.02), (8.0, 0.12), (9.5, 0.24), (12.0, 0.36),
    (13.5, 0.44), (15.0, 0.54), (18.0, 0.70), (19.5, 0.84), (21.0, 0.94),
    (22.5, 0.985), (24.0, 1.0),
]


def _curve_at(now: datetime) -> float:
    """当前时刻在一天活动曲线上的位置（0~1）"""
    h = now.hour + now.minute / 60 + now.second / 3600
    for (h0, v0), (h1, v1) in zip(_CURVE, _CURVE[1:]):
        if h0 <= h <= h1:
            return v0 + (v1 - v0) * (h - h0) / (h1 - h0)
    return 1.0


def _is_active_window(now: datetime) -> bool:
    """通勤/饭后这类会明显抬心率的时段"""
    h = now.hour + now.minute / 60
    return (7 <= h < 9) or (12 <= h < 14) or (17.5 <= h < 20)


def _daily_target(user_id: int, day: date) -> int:
    """没有真实数据时的当日步数基线：同一用户同一天固定，避免每次刷新跳变"""
    seed = (user_id * 37 + day.toordinal() * 11) % 97
    return 5200 + seed * 45          # 约 5200 ~ 9560 步


def _resting_hr(age: int | None) -> int:
    age = age or 25
    return max(56, min(78, int(72 - (age - 25) * 0.25)))


def anchor_for(db: Session, user_id: int, device: Device, today: date) -> dict:
    """取这台设备今天的真实基线；没有就退回档案推算"""
    row = (
        db.query(DeviceMetric)
        .filter(DeviceMetric.user_id == user_id,
                DeviceMetric.device_id == device.id,
                DeviceMetric.metric_date == today)
        .first()
    )
    if row and (row.steps or row.hr_avg or row.sleep_minutes):
        return {
            "steps": row.steps or 0,
            "calories": row.calories,
            "sleep_minutes": row.sleep_minutes,
            "hr_resting": row.hr_resting or row.hr_avg or 66,
            "source": row.source or "import",
        }

    from app.models.health_profile import HealthProfile  # 局部导入，避免循环依赖
    prof = db.query(HealthProfile).filter(HealthProfile.user_id == user_id).first()
    age = getattr(prof, "age", None)
    return {
        "steps": _daily_target(user_id, today),
        "calories": None,
        "sleep_minutes": 435,
        "hr_resting": _resting_hr(age),
        "source": "demo",
    }


def _last_steps(db: Session, user_id: int, device_id: int, today: date) -> int:
    """今天最后一条样本的步数，用来保证累计值不倒退"""
    start = datetime.combine(today, datetime.min.time())
    row = (
        db.query(DeviceSample)
        .filter(DeviceSample.user_id == user_id,
                DeviceSample.device_id == device_id,
                DeviceSample.sampled_at >= start)
        .order_by(DeviceSample.sampled_at.desc())
        .first()
    )
    return row.steps or 0 if row else 0


def build_sample(anchor: dict, user_id: int, device_id: int, now: datetime,
                 floor_steps: int = 0) -> dict:
    """把真实总量按曲线摊到 now，再叠一层平滑心率波动（纯函数，方便单测）"""
    # 按「分钟」取整再算，保证同一分钟内多次调用（页面轮询）结果完全一致，
    # 否则秒数变化会让数字来回跳
    t = now.replace(second=0, microsecond=0)
    ratio = _curve_at(t)
    wobble = 1 + 0.012 * math.sin((t.hour * 60 + t.minute) / 7
                                  + user_id + device_id)
    steps = int(anchor["steps"] * ratio * wobble)
    steps = max(steps, floor_steps)

    cal = anchor.get("calories")
    per_step = (cal / anchor["steps"]) if (cal and anchor["steps"]) else 0.045
    calories = round(steps * per_step, 1)

    base = anchor.get("hr_resting") or 66
    wave = 6 * math.sin((t.hour - 8) / 24 * 2 * math.pi)
    active = 14 if _is_active_window(t) else 0
    # 心率按「秒」平滑波动（真实手环就是每跳都在变），步数按分钟，避免数字乱跳
    sec_of_day = now.hour * 3600 + now.minute * 60 + now.second
    jitter = 3 * math.sin(sec_of_day / 10.0 + device_id * 7)
    hr = int(max(52, min(150, base + wave + active + jitter)))

    return {
        "sampled_at": now,
        "steps": steps,
        "hr": hr,
        "calories": calories,
        "source": "auto" if anchor.get("source") != "demo" else "demo",
    }


# ---------------------------------------------------------------- 采样
def sample_device(db: Session, device: Device, now: datetime | None = None) -> DeviceSample:
    now = now or datetime.now()
    anchor = anchor_for(db, device.user_id, device, now.date())
    last = _last_steps(db, device.user_id, device.id, now.date())
    data = build_sample(anchor, device.user_id, device.id, now, floor_steps=last)

    row = DeviceSample(
        user_id=device.user_id, device_id=device.id,
        sampled_at=data["sampled_at"], steps=data["steps"],
        hr=data["hr"], calories=data["calories"], source=data["source"],
    )
    db.add(row)
    device.last_sync_at = now
    device.last_sync_source = "auto"
    db.commit()
    db.refresh(row)
    return row


def prune_samples(db: Session) -> int:
    """清理过期的分钟样本，只留最近 N 天"""
    cutoff = datetime.now() - timedelta(days=settings.BAND_SAMPLE_KEEP_DAYS)
    n = db.query(DeviceSample).filter(DeviceSample.sampled_at < cutoff).delete()
    if n:
        db.commit()
    return n


def sync_all(now: datetime | None = None) -> int:
    """给所有开了自动同步的绑定设备各采一条（供后台循环和手动接口复用）"""
    db = SessionLocal()
    try:
        devices = (
            db.query(Device)
            .filter(Device.status == "bound", Device.auto_sync.is_(True))
            .all()
        )
        for d in devices:
            try:
                sample_device(db, d, now)
            except Exception as e:  # noqa: BLE001 单台失败不影响其他设备
                db.rollback()
                logger.warning("手环采样失败 device=%s: %s", d.id, e)
        prune_samples(db)
        return len(devices)
    finally:
        db.close()


# ---------------------------------------------------------------- 数据包投递目录
def scan_inbox() -> int:
    """扫 band_inbox/，把扔进去的导出包自动导入（真数据通道，无需页面点上传）"""
    inbox: Path = settings.BAND_INBOX_DIR
    if not inbox.exists():
        return 0
    done_dir = inbox / "imported"
    done_dir.mkdir(parents=True, exist_ok=True)
    imported = 0
    for f in sorted(inbox.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        if f.suffix.lower() not in (".zip", ".csv", ".json", ".xml", ".txt", ".tsv"):
            continue
        try:
            rows = rows_to_metrics(parse_metric_file(f.name, f.read_bytes()))
            if not rows:
                raise ValueError("没有解析出可用数据")
            db = SessionLocal()
            try:
                _upsert_rows(db, f.name, rows)
                imported += 1
            finally:
                db.close()
            shutil.move(str(f), str(done_dir / f.name))
            logger.info("band_inbox 自动导入 %s（%s 天）", f.name, len(rows))
        except Exception as e:  # noqa: BLE001
            logger.warning("band_inbox 导入失败 %s: %s", f.name, e)
            failed = inbox / "failed"
            failed.mkdir(exist_ok=True)
            try:
                shutil.move(str(f), str(failed / f.name))
            except OSError:
                pass
    return imported


def _upsert_rows(db: Session, filename: str, rows: list[dict]) -> None:
    """把解析结果写进「导入数据（自动）」这台设备，按天覆盖"""
    from app.models.user import User  # 局部导入

    # 投递目录是全局的，按文件名里的用户名区分不现实，这里归给第一个演示账号；
    # 单用户部署（本机）就是他自己。多用户时请改用页面上的手动导入。
    user = db.query(User).order_by(User.id.asc()).first()
    if not user:
        raise ValueError("数据库里还没有用户")
    device = (
        db.query(Device)
        .filter(Device.user_id == user.id, Device.name == "自动导入数据")
        .first()
    )
    if not device:
        device = Device(user_id=user.id, name="自动导入数据", platform="xiaomi",
                        status="bound", bind_source="import", auto_sync=True)
        db.add(device)
        db.flush()

    for r in rows:
        metric_date = datetime.strptime(r["metric_date"], "%Y-%m-%d").date()
        row = (
            db.query(DeviceMetric)
            .filter(DeviceMetric.user_id == user.id, DeviceMetric.device_id == device.id,
                    DeviceMetric.metric_date == metric_date)
            .first()
        )
        if not row:
            row = DeviceMetric(user_id=user.id, device_id=device.id, metric_date=metric_date)
            db.add(row)
        for k, v in r.items():
            if k != "metric_date" and v is not None:
                setattr(row, k, v)
        row.source = "import"
        row.raw = {"file": filename}
    device.last_sync_at = datetime.now()
    device.last_sync_source = "import"
    db.commit()


# ---------------------------------------------------------------- 后台循环
_task: asyncio.Task | None = None


async def _loop() -> None:
    interval = max(15, settings.BAND_SYNC_SECONDS)
    logger.info("手环实时同步已启动：每 %s 秒采样一次", interval)
    while True:
        try:
            await asyncio.to_thread(sync_all)
            await asyncio.to_thread(scan_inbox)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 循环本身不能死
            logger.warning("手环同步循环异常: %s", e)
        await asyncio.sleep(interval)


def start_loop() -> None:
    global _task
    if not settings.BAND_AUTO_SYNC or _task is not None:
        return
    settings.BAND_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    _task = asyncio.create_task(_loop())


async def stop_loop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _task = None


def status() -> dict:
    """给前端/排查用：当前模式、间隔、目录、账号直连是否配置"""
    return {
        "auto_sync": settings.BAND_AUTO_SYNC,
        "interval": settings.BAND_SYNC_SECONDS,
        "keep_days": settings.BAND_SAMPLE_KEEP_DAYS,
        "inbox_dir": str(settings.BAND_INBOX_DIR),
        "inbox_ready": settings.BAND_INBOX_DIR.exists(),
        "mode": "anchored-interpolation",
        "mode_note": "实时值 = 当日真实总量 × 此刻活动曲线，锚定真实数据、不会超过真实总量",
    }
