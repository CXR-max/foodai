"""计划上下文：给定日期，找出对应的方案天与当日目标

日程 / 运动 / 我的 都通过这里取「今天该吃多少、喝多少、睡多久、动多少」，
避免各自估算导致同一天不同页面数字对不上。

口径（重要）：
- **判定区间跟随档案**：热量/蛋白/饮水/睡眠/运动下限等每天按当前体重与诉求现算，
  体重掉了 3 kg、诉求从减脂改成健康饮食，区间会立刻跟着变
- **方案餐食不动**：排好的菜和克数仍是生成时的快照，所以当现算目标与快照偏差超过 8%
  （或体重变化 ≥1 kg、诉求变更）时，给出 targets_drift 提示「建议重新生成」，
  而不是让用户拿着旧餐食对着一路变化的标准，永远不达标
- 当日特有字段（训练日上限回补、当日运动下限、用户手改的目标）优先于档案现算
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.check_in import CheckIn
from app.models.diet_plan import DietPlan, PlanDay
from app.models.health_profile import HealthProfile
from app.services import checkup_constraints, plan_targets

# 现算目标与方案快照偏差超过这个比例，就提示「档案变了，建议重新生成」
DRIFT_RATIO = 0.08
# 体重变化达到这个幅度也算偏离（即使热量目标还没超过 8%）
DRIFT_WEIGHT_KG = 1.0

# 应随档案变化的键（判定区间都在这批里）
LIVE_KEYS = (
    "calorie_target", "calorie_min", "calorie_max",
    "protein_min_g", "protein_max_g",
    "fat_min_g", "fat_max_g", "carb_min_g", "carb_max_g",
    "fiber_min_g", "sugar_max_g", "sodium_max_mg",
    "water_min_ml", "water_max_ml",
    "sleep_min_h", "sleep_max_h",
    "exercise_days_per_week", "steps_min", "steps_max",
    "bedtime", "wake_time",
)
# 当日特有字段：来自方案排期或用户手改，不能被档案现算覆盖
DAY_KEYS = (
    "is_training_day", "exercise_burn_target", "exercise_burn_min", "exercise_burn_max",
    "calorie_band_note", "nutrition_snapshot", "calorie_source", "day_theme",
)


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def plan_day_from_tasks(db: Session, user_id: int, day: date) -> PlanDay | None:
    """当天打卡任务绑定的方案天——最可信的一条线索

    任务创建时就把 plan_day_id 写死了。方案被归档、用户又生成了从更晚日期开始的新方案后，
    「按日期 + status 找进行中方案」会扑空，可今天要吃的菜其实还挂在那份旧方案天上。
    所以按日期找不到时，再顺着任务把方案天找回来。
    """
    row = (
        db.query(CheckIn.plan_day_id)
        .filter(
            CheckIn.user_id == user_id,
            CheckIn.check_date == day,
            CheckIn.plan_day_id.isnot(None),
        )
        .order_by(CheckIn.id.desc())
        .first()
    )
    if not row:
        return None
    return db.get(PlanDay, row[0])


def active_plan_day(db: Session, user_id: int, day: date) -> PlanDay | None:
    """当天对应的方案天：优先「进行中」的那份，其次取未归档里最新的

    导入日程会把方案置为进行中、其余归档，所以正常只有一个命中；
    旧数据里可能有多份 active，按创建时间取最新的一份。
    最后兜底到今天打卡任务绑定的方案天（见 plan_day_from_tasks）。
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
    unarchived = (
        base.filter(or_(DietPlan.status.is_(None), DietPlan.status != "archived"))
        .order_by(DietPlan.created_at.desc(), PlanDay.day_index.asc())
        .first()
    )
    if unarchived:
        return unarchived
    return plan_day_from_tasks(db, user_id, day)


def profile_targets(db: Session, user_id: int) -> dict:
    """按档案现算一份完整目标（含处方与依据）"""
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user_id).first()
    if not profile:
        return {}
    ctx = {
        "height_cm": profile.height_cm, "weight_kg": profile.weight_kg,
        "body_fat_pct": profile.body_fat_pct,
        "age": profile.age, "gender": profile.gender,
        "activity_level": profile.activity_level or "moderate",
        "goal": profile.goal or "maintain",
        "daily_target_calories": profile.daily_calorie_target,
        "sleep_time": profile.sleep_time, "wake_time": profile.wake_time,
    }
    # 最近一次体检的实测体重/体脂/基础代谢优先（体检称出来的比自报准）
    checkup_constraints.apply_to_ctx(ctx, checkup_constraints.latest_measured(db, user_id))
    return plan_targets.build_targets(ctx)


def targets_for_day(db: Session, user_id: int, day: date) -> dict:
    """当日目标：档案现算的判定区间 + 当日覆盖（训练日回补 / 用户手改）

    没有方案时直接返回档案现算值；方案存在时以现算值为底，
    只保留当日特有字段与用户显式改过的目标键。
    """
    plan_day = active_plan_day(db, user_id, day)
    current = profile_targets(db, user_id)
    if not plan_day:
        return current

    plan = db.get(DietPlan, plan_day.plan_id)
    snapshot = dict(plan.targets or {}) if plan else {}
    day_override = dict(plan_day.targets or {})
    edited = (plan_day.edit_count or 0) > 0

    if not current:
        merged = dict(snapshot)
        merged.update(day_override)
        return merged

    merged = dict(current)
    # 当日特有字段优先（否则训练日回补会被档案值盖掉）
    for key in DAY_KEYS:
        if key in day_override:
            merged[key] = day_override[key]
        elif key in snapshot:
            merged[key] = snapshot[key]
    # 用户手改过这一天：只认他真正改过的键（与计划级快照不同的那些）
    if edited:
        for key in LIVE_KEYS:
            if key in day_override and day_override[key] != snapshot.get(key):
                merged[key] = day_override[key]
    return merged


def targets_drift(db: Session, user_id: int, plan: DietPlan) -> dict | None:
    """档案变了多少、要不要提示重新生成。没有变化返回 None。

    只做提示，不自动改餐食：排好的菜是快照，悄悄改判定标准会让用户「怎么吃都不达标」。
    """
    current = profile_targets(db, user_id)
    snapshot = plan.targets or {}
    old_target = _num(snapshot.get("calorie_target"))
    new_target = _num(current.get("calorie_target"))
    if not current or not old_target or not new_target:
        return None

    ratio = abs(new_target - old_target) / old_target
    old_weight = _num(snapshot.get("weight_kg"))
    new_weight = _num(current.get("weight_kg"))
    weight_delta = round(new_weight - old_weight, 1) if (old_weight and new_weight) else 0.0
    old_intent = snapshot.get("intent") or snapshot.get("goal")
    new_intent = current.get("intent")
    goal_changed = bool(old_intent and new_intent and old_intent != new_intent)

    if ratio <= DRIFT_RATIO and abs(weight_delta) < DRIFT_WEIGHT_KG and not goal_changed:
        return None

    reasons: list[str] = []
    if goal_changed:
        reasons.append(
            f"诉求从「{plan_targets.GOAL_LABELS.get(old_intent, old_intent)}」"
            f"改为「{plan_targets.GOAL_LABELS.get(new_intent, new_intent)}」"
        )
    if weight_delta:
        reasons.append(f"体重{'增加' if weight_delta > 0 else '减少'} {abs(weight_delta):g} kg")
    if not reasons:
        reasons.append("身体数据有更新")

    old_min = _num(snapshot.get("calorie_min"))
    old_max = _num(snapshot.get("calorie_max"))
    new_min = _num(current.get("calorie_min"))
    new_max = _num(current.get("calorie_max"))

    # 用区间说，不用单个"目标值"：页面在详情里显示的是 1800~2100、日程里是 2120~2480，
    # 再冒出一个"从 2000 调到 2360"会让人以为三个数字互相打架
    if new_min and new_max:
        detail = f"：判定区间已按最新档案调到 {int(new_min)}~{int(new_max)} kcal"
        if old_min and old_max:
            detail += f"（本方案餐食仍按 {int(old_min)}~{int(old_max)} 排）"
        detail += "，建议重新生成一份。"
    else:
        detail = (f"：每日目标已从 {int(old_target)} 调到 {int(new_target)} kcal。"
                  "当前方案仍按原目标排餐，建议重新生成一份。")

    return {
        "changed": True,
        "old_target": int(old_target),
        "new_target": int(new_target),
        "old_range": [int(old_min), int(old_max)] if (old_min and old_max) else None,
        "new_range": [int(new_min), int(new_max)] if (new_min and new_max) else None,
        "ratio": round(ratio, 3),
        "weight_delta": weight_delta,
        "goal_changed": goal_changed,
        "old_intent": old_intent,
        "new_intent": new_intent,
        "message": "，".join(reasons) + detail,
    }
