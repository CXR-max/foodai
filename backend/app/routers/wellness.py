"""睡眠与饮水：记录 + 打卡联动

睡眠一晚一条（sleep_logs），饮水一天多条（water_logs）；
写入后自动维护日程里的 sleep / water 任务状态，达标即完成并发积分。
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.sleep_log import SleepLog
from app.models.user import User
from app.models.water_log import WaterLog
from app.services import day_context, metric_bands, wellness

router = APIRouter(tags=["睡眠与饮水"])


class SleepIn(BaseModel):
    sleep_date: date | None = Field(default=None, description="起床那天，默认今天")
    bedtime: str | None = Field(default=None, max_length=5, description="入睡时间 HH:MM")
    wake_at: str | None = Field(default=None, max_length=5, description="起床时间 HH:MM")
    duration_min: int | None = Field(default=None, ge=0, le=1440, description="直接给时长（分钟）")
    quality_score: int | None = Field(default=None, ge=1, le=5, description="主观质量 1~5")
    deep_min: int | None = None
    rem_min: int | None = None
    awake_min: int | None = None
    note: str | None = Field(default=None, max_length=200)
    source: str = Field(default="manual", pattern="^(manual|device)$")


class WaterIn(BaseModel):
    log_date: date | None = Field(default=None, description="默认今天")
    ml: int = Field(gt=0, le=2000, description="本次饮水量")
    source: str = Field(default="manual", pattern="^(manual|device)$")


def _targets(db: Session, user_id: int, day: date) -> dict:
    return day_context.targets_for_day(db, user_id, day)


@router.get("/sleep")
def list_sleep(
    date_str: str | None = Query(default=None, alias="date"),
    days: int = Query(default=7, ge=1, le=60),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查某晚（date）或最近 N 晚的睡眠记录"""
    if date_str:
        day = date.fromisoformat(date_str)
        log = wellness.day_sleep(db, user.id, day)
        return {"date": str(day), "sleep": wellness.sleep_dict(log)}

    end = date.today()
    start = end - timedelta(days=days - 1)
    rows = (db.query(SleepLog)
            .filter(SleepLog.user_id == user.id,
                    SleepLog.sleep_date >= start, SleepLog.sleep_date <= end)
            .order_by(SleepLog.sleep_date.desc()).all())
    return {"days": days, "records": [wellness.sleep_dict(r) for r in rows]}


@router.post("/sleep")
def save_sleep(data: SleepIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """记录一晚睡眠（手动填写；手环导入走同一接口，source=device）"""
    day = data.sleep_date or date.today()
    plan_day = day_context.active_plan_day(db, user.id, day)
    targets = _targets(db, user.id, day)
    min_h = float(targets.get("sleep_min_h") or 7.0)

    try:
        log, ignored = wellness.upsert_sleep(
            db, user.id,
            sleep_date=day,
            bedtime=data.bedtime,
            wake_at=data.wake_at,
            duration_min=data.duration_min,
            quality_score=data.quality_score,
            deep_min=data.deep_min,
            rem_min=data.rem_min,
            awake_min=data.awake_min,
            note=data.note,
            source=data.source,
            plan_day_id=plan_day.id if plan_day else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = wellness.sync_sleep_checkin(db, user, log, min_h, targets)
    db.commit()
    db.refresh(log)
    return {
        "sleep": wellness.sleep_dict(log),
        "target_min_h": min_h,
        "ignored_device": ignored,
        "checkin": result,
    }


@router.delete("/sleep/{log_id}")
def delete_sleep(log_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    log = db.get(SleepLog, log_id)
    if not log or log.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    db.delete(log)
    db.commit()
    return {"ok": True}


@router.get("/water")
def get_water(
    date_str: str | None = Query(default=None, alias="date"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    day = date.fromisoformat(date_str) if date_str else date.today()
    targets = _targets(db, user.id, day)
    target = int(targets.get("water_min_ml") or 2000)
    rows = (db.query(WaterLog)
            .filter(WaterLog.user_id == user.id, WaterLog.log_date == day)
            .order_by(WaterLog.created_at.asc()).all())
    total = sum(int(r.ml or 0) for r in rows)
    return {
        "date": str(day),
        "total_ml": total,
        "target_ml": target,
        "max_ml": targets.get("water_max_ml"),
        "achieved": metric_bands.achieved(targets, "water", total)[0],
        "records": [{"id": r.id, "ml": r.ml,
                     "created_at": r.created_at.isoformat() if r.created_at else None}
                    for r in rows],
    }


@router.post("/water")
def add_water(data: WaterIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """加一次饮水（常用 200 / 300 / 500 ml），累计到目标后自动完成饮水打卡"""
    day = data.log_date or date.today()
    targets = _targets(db, user.id, day)
    target = int(targets.get("water_min_ml") or 2000)
    result = wellness.add_water(db, user, day, data.ml, target, source=data.source, targets=targets)
    db.commit()
    return {"date": str(day), **result}


@router.delete("/water/{log_id}")
def delete_water(log_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """撤销一次饮水记录"""
    log = db.get(WaterLog, log_id)
    if not log or log.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    day = log.log_date
    db.delete(log)
    db.flush()
    total = wellness.water_total(db, user.id, day)
    db.commit()
    return {"ok": True, "total_ml": total}
