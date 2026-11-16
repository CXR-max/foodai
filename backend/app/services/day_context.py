"""计划上下文：给定日期，找出对应的方案天与当日目标

日程 / 运动 / 我的 都通过这里取「今天该吃多少、喝多少、睡多久、动多少」，
避免各自估算导致同一天不同页面数字对不上。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.diet_plan import DietPlan, PlanDay
from app.models.health_profile import HealthProfile
from app.services import plan_targets


def active_plan_day(db: Session, user_id: int, day: date) -> PlanDay | None:
    """当天对应的方案天：优先「进行中」的那份，其次取未归档里最新的

    导入日程会把方案置为进行中、其余归档，所以正常只有一个命中；
    旧数据里可能有多份 active，按创建时间取最新的一份。
    """
    base = (
        db.query(PlanDay)
        .join(DietPlan, DietPlan.id == PlanDay.plan_id)
        .filter(DietPlan.user_id == user_id, PlanDay.date == day)
    )
    current = (
        base.filter(DietPlan.status == "active")
        .order_by(DietPlan.created_at.desc(), PlanDay.day_index.asc())
        .first()
    )
    if current:
        return current
    return (
        base.filter(or_(DietPlan.status.is_(None), DietPlan.status != "archived"))
        .order_by(DietPlan.created_at.desc(), PlanDay.day_index.asc())
        .first()
    )


def profile_targets(db: Session, user_id: int) -> dict:
    """按档案现算一份完整目标（用于旧方案缺新指标时补上下限）"""
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user_id).first()
    if not profile:
        return {}
    ctx = {
        "height_cm": profile.height_cm, "weight_kg": profile.weight_kg,
        "age": profile.age, "gender": profile.gender,
        "activity_level": profile.activity_level or "moderate",
        "goal": profile.goal or "maintain",
        "daily_target_calories": profile.daily_calorie_target,
        "sleep_time": profile.sleep_time, "wake_time": profile.wake_time,
    }
    return plan_targets.build_targets(ctx)


def targets_for_day(db: Session, user_id: int, day: date) -> dict:
    """当日目标：计划级 targets + 当日覆盖；没有方案时按档案现算一份兜底

    旧方案里可能没有新加的指标（water_max_ml / protein_max_g / exercise_burn_max 等），
    这里用档案现算的完整目标把缺的键补齐，保证「指标区间」对所有方案都成立。
    """
    plan_day = active_plan_day(db, user_id, day)
    merged: dict = {}
    if plan_day:
        plan = db.get(DietPlan, plan_day.plan_id)
        merged = dict(plan.targets or {}) if plan else {}
        merged.update(plan_day.targets or {})

    fallback = profile_targets(db, user_id)
    if merged:
        for key, value in fallback.items():
            merged.setdefault(key, value)
        return merged
    return fallback
