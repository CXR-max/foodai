"""睡眠 / 饮水记录与打卡联动

- 记录层：sleep_logs（一晚一条）、water_logs（一天多条）
- 打卡层：check_ins 里的 sleep / water 任务，由记录是否达标自动置 done
- 手环数据走同一个 upsert：source='device'，本版先留通道，导入器后接
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models.check_in import CheckIn
from app.models.sleep_log import SleepLog
from app.models.user import User
from app.models.water_log import WaterLog
from app.services import points
from app.services.achievements import unlock_all

logger = logging.getLogger("wellness")

CHECK_TYPE = {"sleep": "sleep", "water": "water"}
TASK_LABEL = {"sleep": "睡眠", "water": "饮水"}


def _parse_hhmm(text: str) -> time | None:
    try:
        hour, minute = str(text).strip().split(":")[:2]
        return time(int(hour) % 24, int(minute) % 60)
    except (ValueError, AttributeError):
        return None


def sleep_window(sleep_date: date, bedtime: str, wake_at: str) -> tuple[datetime, datetime]:
    """把 HH:MM 组合成「昨天入睡 → 今天起床」的时间段"""
    bed_t = _parse_hhmm(bedtime)
    wake_t = _parse_hhmm(wake_at)
    if not bed_t or not wake_t:
        raise ValueError("时间格式应为 HH:MM")
    wake_dt = datetime.combine(sleep_date, wake_t)
    bed_dt = datetime.combine(sleep_date, bed_t)
    if bed_t >= wake_t:          # 跨夜：23:20 → 07:10
        bed_dt -= timedelta(days=1)
    return bed_dt, wake_dt


def sleep_dict(log: SleepLog | None) -> dict | None:
    if log is None:
        return None
    return {
        "id": log.id,
        "sleep_date": str(log.sleep_date),
        "bedtime": log.bedtime.strftime("%H:%M") if log.bedtime else None,
        "wake_at": log.wake_at.strftime("%H:%M") if log.wake_at else None,
        "duration_min": log.duration_min,
        "duration_h": round((log.duration_min or 0) / 60, 1) if log.duration_min else None,
        "deep_min": log.deep_min,
        "rem_min": log.rem_min,
        "awake_min": log.awake_min,
        "quality_score": log.quality_score,
        "source": log.source or "manual",
        "note": log.note,
    }


def upsert_sleep(
    db: Session,
    user_id: int,
    *,
    sleep_date: date,
    bedtime: str | None = None,
    wake_at: str | None = None,
    duration_min: int | None = None,
    quality_score: int | None = None,
    deep_min: int | None = None,
    rem_min: int | None = None,
    awake_min: int | None = None,
    note: str | None = None,
    source: str = "manual",
    plan_day_id: int | None = None,
) -> tuple[SleepLog, bool]:
    """写入/更新一晚睡眠，返回 (记录, 是否覆盖了设备数据被拒绝)。

    合并规则：
    - 手环数据后到 → 覆盖手动记录（设备更准）
    - 手动修正过（manual_override）→ 设备数据不再覆盖
    - 用户自己再手填 → 总是覆盖（以人填的为准）
    """
    existing = (db.query(SleepLog)
                .filter(SleepLog.user_id == user_id, SleepLog.sleep_date == sleep_date)
                .first())

    ignored = False
    if existing and existing.source == "manual_override" and source == "device":
        return existing, True

    if duration_min is None:
        if bedtime and wake_at:
            bed_dt, wake_dt = sleep_window(sleep_date, bedtime, wake_at)
            duration_min = int((wake_dt - bed_dt).total_seconds() // 60)
        else:
            raise ValueError("请填写入睡和起床时间，或直接给睡眠时长")

    duration_min = max(0, min(int(duration_min), 24 * 60))
    bed_dt = wake_dt = None
    if bedtime and wake_at:
        bed_dt, wake_dt = sleep_window(sleep_date, bedtime, wake_at)

    log = existing or SleepLog(user_id=user_id, sleep_date=sleep_date)
    log.bedtime = bed_dt or log.bedtime
    log.wake_at = wake_dt or log.wake_at
    log.duration_min = duration_min
    if deep_min is not None:
        log.deep_min = deep_min
    if rem_min is not None:
        log.rem_min = rem_min
    if awake_min is not None:
        log.awake_min = awake_min
    if quality_score is not None:
        log.quality_score = max(1, min(int(quality_score), 5))
    if note is not None:
        log.note = note[:200]
    if plan_day_id is not None:
        log.plan_day_id = plan_day_id
    log.source = "manual_override" if (existing and existing.source == "device" and source == "manual") else source
    log.updated_at = datetime.now()
    if existing is None:
        db.add(log)
    db.flush()
    return log, ignored


def _sync_checkin(
    db: Session,
    user: User,
    day: date,
    task_key: str,
    achieved: bool,
    note: str | None = None,
) -> CheckIn | None:
    """按记录达标情况维护 check_ins 里的任务状态；达标时按每日一次发积分。"""
    task = (db.query(CheckIn)
            .filter(CheckIn.user_id == user.id, CheckIn.check_date == day,
                    CheckIn.task_key == task_key)
            .first())

    if achieved:
        if task is None:
            task = CheckIn(user_id=user.id, check_date=day, check_type=CHECK_TYPE[task_key],
                           task_key=task_key, status="done")
            db.add(task)
            db.flush()
        elif task.status == "done":
            return task
        else:
            task.status = "done"

        action = "task_sleep" if task_key == "sleep" else "task_water"
        pts = points.award(
            db, user, action, once_per_day=True,
            description=f"完成{TASK_LABEL[task_key]}打卡",
            ref_type="checkin", ref_id=task.id,
        )
        if pts:
            task.points_awarded = (task.points_awarded or 0) + pts
        return task

    if task is None:
        task = CheckIn(user_id=user.id, check_date=day, check_type=CHECK_TYPE[task_key],
                       task_key=task_key, status="pending")
        db.add(task)
        db.flush()
    if note is not None and task.status != "done":
        task.note = note[:200]
    return task


def sync_sleep_checkin(db: Session, user: User, log: SleepLog, min_hours: float) -> dict:
    """睡眠达标判定：达到计划下限的 90% 记为完成（避免 6.9h vs 7h 卡死）"""
    required_min = int(round(float(min_hours or 7.0) * 60))
    achieved = (log.duration_min or 0) >= required_min * 0.9
    note = None
    if not achieved:
        note = f"睡眠 {round((log.duration_min or 0) / 60, 1)}h，低于计划 {min_hours:g}h"
    task = _sync_checkin(db, user, log.sleep_date, "sleep", achieved, note)
    return {
        "achieved": achieved,
        "required_min": required_min,
        "duration_min": log.duration_min,
        "checkin_id": task.id if task else None,
    }


def water_total(db: Session, user_id: int, day: date) -> int:
    rows = (db.query(WaterLog)
            .filter(WaterLog.user_id == user_id, WaterLog.log_date == day).all())
    return sum(int(r.ml or 0) for r in rows)


def add_water(
    db: Session,
    user: User,
    day: date,
    ml: int,
    target_ml: int,
    source: str = "manual",
) -> dict:
    amount = max(0, min(int(ml), 5000))
    if amount:
        db.add(WaterLog(user_id=user.id, log_date=day, ml=amount, source=source))
        db.flush()
    total = water_total(db, user.id, day)
    achieved = target_ml > 0 and total >= target_ml
    note = None if achieved else f"已喝 {total} / {target_ml} ml"
    task = _sync_checkin(db, user, day, "water", achieved, note)
    if achieved:
        unlock_all(db, user)
    return {
        "total_ml": total,
        "target_ml": target_ml,
        "achieved": achieved,
        "checkin_id": task.id if task else None,
    }


def day_sleep(db: Session, user_id: int, day: date) -> SleepLog | None:
    return (db.query(SleepLog)
            .filter(SleepLog.user_id == user_id, SleepLog.sleep_date == day)
            .first())
