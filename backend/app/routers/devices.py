"""智能穿戴设备接入路由

三类入口，最终都落到同一张 `device_metrics` 表：
1. `POST /devices/{id}/sync`：手机浏览器蓝牙直连后上报（source=ble）
2. `POST /devices/import`：上传小米运动健康/Zepp/Apple 健康导出的文件（source=import）
3. `POST /devices/{id}/sync` 也能被第三方脚本直接调用（source=api），带用户 token 即可

另外提供 `/devices/summary` 给仪表盘用，`/{id}/to-checkin` 把当天步数
一键转成运动打卡（走同一套个性化消耗模型，保证与手动打卡口径一致）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.check_in import CheckIn
from app.models.device import Device, DeviceMetric, DeviceSample
from app.models.food_record import FoodRecord
from app.models.health_profile import HealthProfile
from app.models.user import User
from app.schemas.device import DeviceBindIn, MetricIn, ToCheckinIn
from app.services import band_live, points
from app.services.device_import import parse_metric_file, rows_to_metrics, summarize_rows
from app.services.met import personalized_burn
from app.services.nutrition import ACTIVITY_FACTORS, calc_bmr

router = APIRouter(prefix="/devices", tags=["智能设备"])

# 默认每日步数目标（可后续做成用户可配）
DEFAULT_STEP_GOAL = 8000
# 默认睡眠目标 7.5 小时（分钟）
DEFAULT_SLEEP_GOAL_MINUTES = 450
MAX_IMPORT_BYTES = 60 * 1024 * 1024   # 一年期手环数据包（zip）通常 10~40MB


# ------------------------------------------------------------------ 工具
def _profile_bundle(db: Session, user_id: int) -> tuple[dict, HealthProfile | None]:
    """取出计算消耗需要的档案字段"""
    p = db.query(HealthProfile).filter(HealthProfile.user_id == user_id).first()
    if not p:
        return {}, None
    return ({c: getattr(p, c) for c in
             ("gender", "age", "weight_kg", "height_cm", "body_fat_pct", "activity_level")}, p)


def _derive(metric: dict, profile: dict) -> dict:
    """手环只给步数时，用身高估步幅、用体重估消耗，补齐距离与热量"""
    steps = metric.get("steps")
    if not steps:
        return metric
    height = profile.get("height_cm") or 170
    weight = profile.get("weight_kg") or 60
    # 步幅 ≈ 身高 × 0.415（成年人步行经验系数）
    if metric.get("distance_km") is None:
        stride_m = height * 0.415 / 100
        metric["distance_km"] = round(steps * stride_m / 1000, 2)
    # 步行能耗 ≈ 0.5 kcal / kg / km
    if metric.get("calories") is None and metric.get("distance_km"):
        metric["calories"] = round(metric["distance_km"] * weight * 0.5, 1)
    if metric.get("active_minutes") is None and metric.get("distance_km"):
        # 按 4.5 km/h 的日常步行速度折算活动时长
        metric["active_minutes"] = int(round(metric["distance_km"] / 4.5 * 60))
    return metric


def _metric_dict(m: DeviceMetric) -> dict:
    return {
        "id": m.id,
        "metric_date": m.metric_date.isoformat() if m.metric_date else None,
        "steps": m.steps, "distance_km": m.distance_km, "calories": m.calories,
        "active_minutes": m.active_minutes, "floors": m.floors,
        "hr_avg": m.hr_avg, "hr_min": m.hr_min, "hr_max": m.hr_max, "hr_resting": m.hr_resting,
        "sleep_minutes": m.sleep_minutes,
        "deep_sleep_minutes": m.deep_sleep_minutes,
        "light_sleep_minutes": m.light_sleep_minutes,
        "rem_sleep_minutes": m.rem_sleep_minutes,
        "awake_minutes": m.awake_minutes,
        "spo2": m.spo2, "stress": m.stress,
        "source": m.source,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


def _device_dict(d: Device) -> dict:
    return {
        "id": d.id, "name": d.name, "model": d.model, "platform": d.platform,
        "mac": d.mac, "ble_name": d.ble_name,
        "battery": d.battery, "firmware": d.firmware,
        "status": d.status, "bind_source": d.bind_source,
        "last_sync_at": d.last_sync_at.isoformat() if d.last_sync_at else None,
        "last_sync_source": d.last_sync_source,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def _upsert_metric(db: Session, user: User, device: Device, payload: dict,
                   source: str, profile: dict) -> DeviceMetric:
    """按「用户+设备+日期」覆盖更新；只覆盖本次带了的字段，避免补一条步数把心率冲掉"""
    day = date.fromisoformat(payload["metric_date"])
    payload = _derive(dict(payload), profile)

    row = (db.query(DeviceMetric)
           .filter(DeviceMetric.user_id == user.id, DeviceMetric.device_id == device.id,
                   DeviceMetric.metric_date == day).first())
    if row is None:
        row = DeviceMetric(user_id=user.id, device_id=device.id, metric_date=day,
                           source=source, created_at=datetime.now())
        db.add(row)

    fields = ("steps", "distance_km", "calories", "active_minutes", "floors",
              "hr_avg", "hr_min", "hr_max", "hr_resting", "sleep_minutes",
              "deep_sleep_minutes", "light_sleep_minutes", "rem_sleep_minutes",
              "awake_minutes", "spo2", "stress")
    for f in fields:
        v = payload.get(f)
        if v is not None:
            setattr(row, f, v)
    row.source = source
    if payload.get("raw") is not None:
        row.raw = payload["raw"]
    row.updated_at = datetime.now()

    # ---- 数据互通：手环睡眠镜像进主分支睡眠体系（sleep_logs, source=device）----
    # 这样「我的」页三环的睡眠环、今日数据的睡眠小时都跟着手环走，用户不用再手工录一遍。
    # 手动改过的记录（manual_override）由 upsert_sleep 自己保护，不会被设备数据覆盖。
    if row.sleep_minutes:
        try:
            from app.services import wellness as wellness_service
            wellness_service.upsert_sleep(
                db, user.id,
                sleep_date=day,
                duration_min=int(row.sleep_minutes),
                deep_min=int(row.deep_sleep_minutes) if row.deep_sleep_minutes else None,
                rem_min=int(row.rem_sleep_minutes) if row.rem_sleep_minutes else None,
                awake_min=int(row.awake_minutes) if row.awake_minutes else None,
                source="device",
            )
        except Exception:  # noqa: BLE001 —— 镜像失败不能影响手环指标本身入库
            pass
    return row


def _touch_device(db: Session, device: Device, source: str, battery: int | None = None):
    device.last_sync_at = datetime.now()
    device.last_sync_source = source
    if battery is not None:
        device.battery = battery
    if device.status in (None, "offline"):
        device.status = "bound"


def _owned_device(db: Session, user: User, device_id: int) -> Device:
    d = db.query(Device).filter(Device.id == device_id, Device.user_id == user.id).first()
    if not d:
        raise HTTPException(404, "设备不存在或不属于当前账号")
    return d


# ------------------------------------------------------------------ 设备
@router.get("")
def list_devices(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """我的设备列表 + 每台设备今天的数据 + 今日汇总"""
    devices = (db.query(Device).filter(Device.user_id == user.id)
               .order_by(Device.created_at.desc()).all())
    today = date.today()
    rows = (db.query(DeviceMetric)
            .filter(DeviceMetric.user_id == user.id, DeviceMetric.metric_date == today).all())
    by_device = {r.device_id: _metric_dict(r) for r in rows}

    latest = (db.query(func.max(DeviceMetric.metric_date))
              .filter(DeviceMetric.user_id == user.id).scalar())

    return {
        "items": [{**_device_dict(d), "today": by_device.get(d.id)} for d in devices],
        "today": _merge_today(rows),
        "step_goal": DEFAULT_STEP_GOAL,
        "last_metric_date": latest.isoformat() if latest else None,
    }


def _merge_today(rows: list[DeviceMetric]) -> dict:
    """多设备同日数据合并：步数等取最大值（防两台设备重复计数），心率取平均"""
    if not rows:
        return {"steps": 0, "distance_km": 0, "calories": 0, "active_minutes": 0,
                "hr_avg": None, "sleep_minutes": None, "has_data": False}
    def mx(attr):
        vals = [getattr(r, attr) for r in rows if getattr(r, attr) is not None]
        return max(vals) if vals else None
    def avg(attr):
        vals = [getattr(r, attr) for r in rows if getattr(r, attr) is not None]
        return round(sum(vals) / len(vals)) if vals else None
    sleep_vals = [r.sleep_minutes for r in rows if r.sleep_minutes]
    # 展示用：数据来源取"步数最多的那台设备"的来源，页头据此显示「演示数据」标签
    main = max(rows, key=lambda r: (r.steps or 0))
    return {
        "steps": mx("steps") or 0,
        "distance_km": mx("distance_km") or 0,
        "calories": mx("calories") or 0,
        "active_minutes": mx("active_minutes") or 0,
        "hr_avg": avg("hr_avg"),
        "sleep_minutes": max(sleep_vals) if sleep_vals else None,
        "source": main.source,
        "has_data": True,
    }


@router.post("")
def bind_device(data: DeviceBindIn, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """绑定一台设备；同一 MAC 重复绑定直接复用（幂等）"""
    exists = None
    if data.mac:
        exists = (db.query(Device).filter(Device.user_id == user.id,
                                          Device.mac == data.mac).first())
    if exists:
        exists.name = data.name or exists.name
        exists.model = data.model or exists.model
        exists.battery = data.battery if data.battery is not None else exists.battery
        exists.status = "bound"
        exists.ble_name = data.ble_name or exists.ble_name
        db.commit()
        db.refresh(exists)
        return {"device": _device_dict(exists), "reused": True}

    d = Device(user_id=user.id, name=data.name or "小米手环", model=data.model,
               platform=data.platform, mac=data.mac, ble_name=data.ble_name,
               battery=data.battery, firmware=data.firmware,
               status="bound", bind_source=data.bind_source,
               created_at=datetime.now())
    db.add(d)
    db.commit()
    db.refresh(d)
    return {"device": _device_dict(d), "reused": False}


@router.patch("/{device_id}")
def update_device(device_id: int, data: DeviceBindIn,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    d = _owned_device(db, user, device_id)
    for f in ("name", "model", "ble_name", "firmware"):
        v = getattr(data, f)
        if v:
            setattr(d, f, v)
    if data.battery is not None:
        d.battery = data.battery
    db.commit()
    return _device_dict(d)


@router.delete("/{device_id}")
def remove_device(device_id: int, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    d = _owned_device(db, user, device_id)
    db.query(DeviceMetric).filter(DeviceMetric.device_id == d.id,
                                  DeviceMetric.user_id == user.id).delete()
    db.delete(d)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ 同步
@router.post("/{device_id}/sync")
def sync_metrics(device_id: int, body: dict = Body(...),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """上报指标（单条或 `{"metrics": [...]}` 批量）。按日期覆盖更新，重复推送不会产生重复行。"""
    device = _owned_device(db, user, device_id)
    profile, _ = _profile_bundle(db, user.id)

    # 手动区分「单条」和「批量」：直接声明 SyncIn | MetricIn 会让 pydantic
    # 把批量体当单条吃掉（MetricIn 字段全可选），所以这里按 key 判断
    if isinstance(body.get("metrics"), list):
        raw_items = body["metrics"]
    else:
        raw_items = [body]
    if not raw_items:
        raise HTTPException(400, "没有要同步的数据")

    metrics: list[MetricIn] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            metrics.append(MetricIn(**item))
        except ValidationError as e:
            raise HTTPException(422, f"字段格式不对：{e.errors()[0].get('msg')}") from e
    if not metrics:
        raise HTTPException(400, "没有可解析的指标")

    written = []
    for m in metrics:
        payload = m.model_dump()
        payload.pop("source", None)
        if not payload.get("metric_date"):
            payload["metric_date"] = date.today().isoformat()
        written.append(_upsert_metric(db, user, device, payload, m.source or "ble", profile))

    _touch_device(db, device, metrics[-1].source or "ble",
                  body.get("battery") if isinstance(body.get("battery"), int) else None)
    db.commit()
    for r in written:
        db.refresh(r)
    return {"synced": len(written), "dates": [r.metric_date.isoformat() for r in written],
            "device": _device_dict(device)}


@router.get("/{device_id}/metrics")
def list_metrics(device_id: int,
                 days: int = Query(30, ge=1, le=366),
                 start: str | None = Query(None), end: str | None = Query(None),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    device = _owned_device(db, user, device_id)
    q = db.query(DeviceMetric).filter(DeviceMetric.user_id == user.id,
                                     DeviceMetric.device_id == device.id)
    if start:
        q = q.filter(DeviceMetric.metric_date >= date.fromisoformat(start[:10]))
    if end:
        q = q.filter(DeviceMetric.metric_date <= date.fromisoformat(end[:10]))
    if not start and not end:
        q = q.filter(DeviceMetric.metric_date >= date.today() - timedelta(days=days - 1))
    rows = q.order_by(DeviceMetric.metric_date).all()
    return {"device": _device_dict(device), "items": [_metric_dict(r) for r in rows]}


@router.get("/summary")
def device_summary(days: int = Query(7, ge=1, le=90),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """今日 + 近 N 天的手环数据汇总（仪表盘/设备页头部用）"""
    today = date.today()
    since = today - timedelta(days=days - 1)
    rows = (db.query(DeviceMetric)
            .filter(DeviceMetric.user_id == user.id, DeviceMetric.metric_date >= since)
            .order_by(DeviceMetric.metric_date).all())

    by_day: dict[str, list[DeviceMetric]] = {}
    for r in rows:
        by_day.setdefault(r.metric_date.isoformat(), []).append(r)

    series = []
    for i in range(days):
        d = (since + timedelta(days=i)).isoformat()
        merged = _merge_today(by_day.get(d, []))
        series.append({"date": d, **merged})

    today_data = series[-1] if series else {}
    with_data = [s for s in series if s["has_data"]]
    return {
        "today": {k: v for k, v in today_data.items() if k != "has_data"},
        "series": series,
        "step_goal": DEFAULT_STEP_GOAL,
        "days_with_data": len(with_data),
        "avg_steps": int(sum(s["steps"] for s in with_data) / len(with_data)) if with_data else 0,
        "total_steps": sum(s["steps"] for s in with_data),
        "avg_sleep_minutes": (int(sum(s["sleep_minutes"] or 0 for s in with_data
                                      if s["sleep_minutes"]) /
                                 max(1, len([s for s in with_data if s["sleep_minutes"]])))
                              if with_data else None),
        "avg_hr": (int(sum(s["hr_avg"] for s in with_data if s["hr_avg"]) /
                       max(1, len([s for s in with_data if s["hr_avg"]])))
                   if with_data else None),
    }


# ------------------------------------------------------------------ 今日概览三环
def _sleep_comment(total: int, deep_pct: float) -> str:
    """按总时长 + 深睡占比给一句人话点评"""
    hours = total / 60
    if hours < 6:
        base = "睡眠偏少，白天容易犯困，建议早点休息"
    elif hours < 7:
        base = "睡眠略短于目标，可以再补一会儿"
    elif hours <= 9:
        base = "睡眠时长达标，作息规律"
    else:
        base = "睡得有点多，注意别打乱节律"
    if deep_pct >= 20:
        deep = "深睡占比不错，恢复效果较好"
    elif deep_pct >= 13:
        deep = "深睡占比正常"
    else:
        deep = "深睡偏少，睡前少看手机、保持安静环境有助改善"
    return f"{base}；{deep}"


@router.get("/rings")
def device_rings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """今日概览三环（摄入 / 消耗 / 睡眠）+ 最近一晚睡眠结构

    给「我的设备」页的 Keep 风格三环卡和睡眠卡用：
    - 饮食环：今日拍照记录的摄入合计 / 档案每日热量目标
    - 消耗环：手环活动消耗 + 运动打卡消耗 / (TDEE - BMR) 活动部分目标
    - 睡眠环：最近一晚手环睡眠时长 / 7.5 小时
    """
    today = date.today()

    # ---- 饮食环 ----
    intake = (db.query(func.coalesce(func.sum(FoodRecord.calories), 0.0))
              .filter(FoodRecord.user_id == user.id, FoodRecord.eaten_date == today).scalar())
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    intake_target = (profile.daily_calorie_target
                     if profile and profile.daily_calorie_target else 2000)

    # ---- 消耗环 ----
    metric_today = (db.query(DeviceMetric)
                    .filter(DeviceMetric.user_id == user.id,
                            DeviceMetric.metric_date == today).all())
    band_burn = sum(m.calories or 0 for m in metric_today)
    checkin_burn = (db.query(func.coalesce(func.sum(CheckIn.calories_burned), 0.0))
                    .filter(CheckIn.user_id == user.id, CheckIn.check_type == "exercise",
                            CheckIn.status == "done", CheckIn.check_date == today).scalar())
    bmr = None
    burn_target = 300
    if profile:
        bmr = calc_bmr(profile.weight_kg, profile.height_cm, profile.age, profile.gender)
        if bmr:
            factor = ACTIVITY_FACTORS.get(profile.activity_level or "moderate", 1.55)
            # 活动消耗目标 = TDEE - BMR（总消耗里“动起来”的那部分）
            burn_target = max(200, round(bmr * (factor - 1)))
    burn_value = round(band_burn + (checkin_burn or 0))

    # ---- 睡眠环 + 睡眠结构卡：最近一晚有睡眠数据的日子（14 天内）----
    last = (db.query(DeviceMetric)
            .filter(DeviceMetric.user_id == user.id,
                    DeviceMetric.metric_date >= today - timedelta(days=13),
                    DeviceMetric.metric_date <= today,
                    DeviceMetric.sleep_minutes.isnot(None))
            .order_by(DeviceMetric.metric_date.desc()).first())
    sleep_value = None
    sleep_detail = None
    if last and (last.sleep_minutes or 0) > 0:
        total = last.sleep_minutes
        deep = last.deep_sleep_minutes or 0
        light = last.light_sleep_minutes or 0
        rem = last.rem_sleep_minutes or 0
        awake = last.awake_minutes or 0
        deep_pct = round(deep / total * 100, 1) if total else 0.0
        sleep_value = total
        sleep_detail = {
            "date": last.metric_date.isoformat(),
            "total": total, "deep": deep, "light": light,
            "rem": rem, "awake": awake,
            "deep_pct": deep_pct,
            "comment": _sleep_comment(total, deep_pct),
        }

    return {
        "date": today.isoformat(),
        "intake": {"value": round(float(intake or 0), 1), "target": intake_target, "unit": "kcal"},
        "burn": {"value": burn_value, "target": burn_target, "bmr": bmr, "unit": "kcal"},
        "sleep": {"value": sleep_value, "target": DEFAULT_SLEEP_GOAL_MINUTES, "unit": "min"},
        "sleep_detail": sleep_detail,
    }


# ------------------------------------------------------------------ 导入
@router.post("/import/preview")
async def import_preview(file: UploadFile = File(...),
                         user: User = Depends(get_current_user)):
    """只解析不入库，让用户先确认「读到了几天、哪些字段」"""
    content = await file.read()
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "文件超过 60MB，请先在手机上裁剪导出范围")
    try:
        rows = rows_to_metrics(parse_metric_file(file.filename or "", content))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    info = summarize_rows(rows)
    info["rows"] = len(rows)
    return info


@router.post("/import")
async def import_file(file: UploadFile = File(...),
                      device_id: int | None = Query(None),
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """解析并入库。没指定设备时自动建一台「导入设备」承接。"""
    content = await file.read()
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "文件超过 60MB，请先在手机上裁剪导出范围")
    try:
        rows = rows_to_metrics(parse_metric_file(file.filename or "", content))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not rows:
        raise HTTPException(400, "文件里没有识别出可用数据，请检查表头是否包含「日期/步数」等列")

    device = (_owned_device(db, user, device_id) if device_id
              else db.query(Device).filter(Device.user_id == user.id,
                                           Device.bind_source == "import").first())
    if device is None:
        device = Device(user_id=user.id, name="导入数据", model="文件导入",
                        platform="other", status="bound", bind_source="import",
                        created_at=datetime.now())
        db.add(device)
        db.flush()

    profile, _ = _profile_bundle(db, user.id)
    for row in rows:
        row["metric_date"] = row["metric_date"][:10]
        _upsert_metric(db, user, device, row, "import", profile)
    _touch_device(db, device, "import")
    db.commit()
    db.refresh(device)
    return {"imported": len(rows), "device": _device_dict(device),
            "start": rows[0]["metric_date"], "end": rows[-1]["metric_date"]}


# ------------------------------------------------------------------ 步数转打卡
@router.post("/{device_id}/to-checkin")
def steps_to_checkin(device_id: int, data: ToCheckinIn,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """把当天手环步数转成一次运动打卡（消耗按个性化模型算，可追溯）"""
    device = _owned_device(db, user, device_id)
    day = date.fromisoformat(data.metric_date[:10]) if data.metric_date else date.today()
    row = (db.query(DeviceMetric)
           .filter(DeviceMetric.user_id == user.id, DeviceMetric.device_id == device.id,
                   DeviceMetric.metric_date == day).first())
    if not row or not row.steps:
        raise HTTPException(400, f"{day} 没有手环步数，先同步数据再来打卡")

    exists = (db.query(CheckIn)
              .filter(CheckIn.user_id == user.id, CheckIn.check_date == day,
                      CheckIn.check_type == "exercise", CheckIn.task_key == "device_steps").first())
    if exists:
        raise HTTPException(409, "这一天已经用步数打过卡了")

    profile, _ = _profile_bundle(db, user.id)
    distance = row.distance_km or round((row.steps or 0) * 0.7 / 1000, 2)
    duration = data.duration_min or max(5, int(round(distance / 4.5 * 60)))
    burn = personalized_burn(data.exercise_type or "快走", duration, profile, "light")

    c = CheckIn(
        user_id=user.id, check_date=day, check_type="exercise", task_key="device_steps",
        status="done", exercise_type=data.exercise_type or "快走", duration_min=duration,
        calories_burned=burn["calories_burned"], intensity="light",
        met_used=burn["met"],
        burn_detail={"base": burn["base"], "factors": burn["factors"], "steps": burn["steps"],
                     "low": burn["low"], "high": burn["high"], "formula": burn["formula"],
                     "source": "device", "device_name": device.name, "steps": row.steps},
        note=f"来自手环 {device.name}：{row.steps} 步 / {distance} km",
    )
    db.add(c)
    pts = points.award(db, user, "manual_checkin", once_per_day=True,
                       description=f"手环步数打卡 {row.steps} 步", ref_type="exercise") or 0
    c.points_awarded = pts
    db.commit()
    db.refresh(c)
    return {"checkin_id": c.id, "calories_burned": c.calories_burned,
            "duration_min": duration, "steps": row.steps, "points_awarded": pts,
            "burn_detail": c.burn_detail}


# ------------------------------------------------------------------ 演示数据
@router.post("/simulate")
def simulate(days: int = Query(14, ge=1, le=60), device_id: int | None = Query(None),
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """生成一段仿真手环数据，用于没有实物/蓝牙不可用时演示。

    明确标记 source=demo，界面上会显示「演示数据」，不会冒充真实采集。
    """
    import math
    import random

    device = (_owned_device(db, user, device_id) if device_id
              else db.query(Device).filter(Device.user_id == user.id,
                                           Device.platform == "xiaomi").first())
    if device is None:
        device = Device(user_id=user.id, name="小米手环 8", model="Mi Band 8",
                        platform="xiaomi", status="bound", bind_source="manual",
                        battery=76, firmware="1.0.9.24", created_at=datetime.now())
        db.add(device)
        db.flush()

    profile, _ = _profile_bundle(db, user.id)
    rnd = random.Random(f"{user.id}-{days}")   # 固定种子：同一用户重复生成结果一致
    today = date.today()
    created = 0
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            steps = rnd.randint(6000, 15000)      # 周末更爱动
        else:
            steps = rnd.randint(3500, 11000)
        if i == 0:
            # 今天的数据按时间进度生成，避免"今天已经走了 9000 步"的违和感
            progress = max(0.15, (datetime.now().hour * 60 + datetime.now().minute) / 1440)
            steps = int(steps * progress)
        hr = rnd.randint(62, 78)
        sleep = rnd.randint(360, 480) - (60 if d.weekday() >= 5 else 0)
        deep = int(sleep * rnd.uniform(0.18, 0.26))
        light = sleep - deep - int(sleep * 0.2)
        rem = sleep - deep - light
        spo2 = round(rnd.uniform(96.0, 99.0), 1)
        _upsert_metric(db, user, device, {
            "metric_date": d.isoformat(), "steps": steps,
            "hr_avg": hr, "hr_min": hr - rnd.randint(8, 16), "hr_max": hr + rnd.randint(40, 70),
            "hr_resting": hr - rnd.randint(2, 8),
            "sleep_minutes": sleep, "deep_sleep_minutes": deep,
            "light_sleep_minutes": max(0, light), "rem_sleep_minutes": max(0, rem),
            "awake_minutes": rnd.randint(8, 30),
            "spo2": spo2, "stress": rnd.randint(20, 55),
            "floors": rnd.randint(2, 12),
            "raw": {"simulated": True, "wave": round(math.sin(i / 3.0), 3)},
        }, "demo", profile)
        created += 1

    device.battery = rnd.randint(45, 95)
    _touch_device(db, device, "demo")
    db.commit()
    db.refresh(device)
    return {"generated": created, "device": _device_dict(device)}


# ------------------------------------------------------------------ 实时
@router.get("/live/status")
def live_status(user: User = Depends(get_current_user)):
    """后台同步是否在跑、间隔多久、投递目录在哪"""
    return band_live.status()


@router.get("/live")
def live(device_id: int | None = Query(None),
         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """「实时」数据：最新一条样本 + 今天的分时曲线

    前端轮询即可看到数字在走（设备页 5 秒、仪表盘 10 秒，标签页不可见时暂停）。没有样本时现场算一条（不落库），
    保证页面任何时刻打开都有数，不会显示 0。
    """
    q = db.query(Device).filter(Device.user_id == user.id)
    device = _owned_device(db, user, device_id) if device_id else q.order_by(Device.id.asc()).first()
    base = {
        "enabled": settings.BAND_AUTO_SYNC,
        "interval": settings.BAND_SYNC_SECONDS,
        "device": _device_dict(device) if device else None,
        "today": None,
        "samples": [],
        "updated_at": None,
        "mode": band_live.status()["mode"],
    }
    if not device:
        return base

    today = date.today()
    start = datetime.combine(today, datetime.min.time())
    rows = (db.query(DeviceSample)
            .filter(DeviceSample.user_id == user.id, DeviceSample.device_id == device.id,
                    DeviceSample.sampled_at >= start)
            .order_by(DeviceSample.sampled_at.asc()).all())

    anchor = band_live.anchor_for(db, user.id, device, today)
    latest = rows[-1] if rows else None
    if latest:
        steps, hr, cal, at = latest.steps, latest.hr, latest.calories, latest.sampled_at
        src = latest.source
    else:
        now = datetime.now()
        calc = band_live.build_sample(anchor, user.id, device.id, now)
        steps, hr, cal, at, src = calc["steps"], calc["hr"], calc["calories"], now, calc["source"]

    base["today"] = {
        "date": today.isoformat(),
        "steps": steps or 0,
        "steps_goal": DEFAULT_STEP_GOAL,
        "hr": hr,
        "calories": cal or 0,
        "sleep_minutes": anchor.get("sleep_minutes"),
        "hr_resting": anchor.get("hr_resting"),
        "anchor_steps": anchor.get("steps") or 0,
        "anchor_source": anchor.get("source"),
        "is_demo": (anchor.get("source") == "demo") or src == "demo",
    }
    base["samples"] = [
        {"t": r.sampled_at.strftime("%H:%M"), "steps": r.steps or 0, "hr": r.hr}
        for r in rows[-120:]
    ]
    base["updated_at"] = at.isoformat(timespec="seconds") if at else None
    base["auto_sync"] = bool(device.auto_sync)
    return base


@router.post("/auto-sync")
def set_auto_sync(payload: dict = Body(...),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """页面上的「自动同步」开关，只影响这一台设备"""
    device_id = payload.get("device_id")
    enabled = bool(payload.get("enabled", True))
    if device_id:
        device = _owned_device(db, user, int(device_id))
    else:
        device = (db.query(Device).filter(Device.user_id == user.id)
                  .order_by(Device.id.asc()).first())
        if not device:
            raise HTTPException(404, "还没有绑定设备")
    device.auto_sync = enabled
    db.commit()
    db.refresh(device)
    return {"device_id": device.id, "auto_sync": bool(device.auto_sync)}


@router.post("/live/tick")
def tick_now(user: User = Depends(get_current_user)):
    """立即采一条（调试/演示用，不用等后台那一分钟）"""
    n = band_live.sync_all()
    return {"sampled": n, "at": datetime.now().isoformat(timespec="seconds")}
