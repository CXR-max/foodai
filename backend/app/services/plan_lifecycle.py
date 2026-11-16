"""方案生命周期：进行中唯一化 / 过期方案顺延 / 热量超限折算成运动

产品口径（用户确认）：
- 把方案「导入日程」= 这份方案开始生效 → 置为「进行中」
- 其它进行中的方案（含更早那几份）→ 自动归档到「历史方案」，仍可查看 / 沿用 / 再导入
- 历史方案的日期早已过去 → 导入时整份顺延到今天，否则任务会落在过去、打不了卡
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.check_in import CheckIn
from app.models.diet_plan import DietPlan, PlanDay
from app.services import plan_review, plan_targets

logger = logging.getLogger("plan_lifecycle")


def activate_plan(db: Session, user_id: int, plan: DietPlan) -> dict:
    """把某份方案置为「进行中」，其余进行中的归档为历史方案（不提交事务）。

    若这份方案此前已归档（= 从历史方案重新启用），在 basis_snapshot 里记一笔
    reactivated_at / reactivate_count，列表据此显示「已重新启用」。
    """
    was_archived = plan.status == "archived"
    others = (
        db.query(DietPlan)
        .filter(DietPlan.user_id == user_id, DietPlan.status == "active", DietPlan.id != plan.id)
        .all()
    )
    for old in others:
        old.status = "archived"
        # 停用的方案留一份执行评价，历史方案里能看这一期做得怎么样
        try:
            plan_review.store_review(db, old)
        except Exception:  # noqa: BLE001  评价失败不能挡住归档 / 导入
            logger.exception("方案 %s 生成停用评价失败", old.id)
    plan.status = "active"

    reactivated_at = None
    snapshot = dict(plan.basis_snapshot or {})
    if was_archived:
        reactivated_at = datetime.now().isoformat(timespec="seconds")
        snapshot["reactivated_at"] = reactivated_at
        snapshot["reactivate_count"] = int(snapshot.get("reactivate_count") or 0) + 1
        logger.info("方案 %s 从历史方案重新启用（第 %s 次）",
                    plan.id, snapshot["reactivate_count"])
    if plan.review:
        # 重新启用 = 新一期：上一期的评价留档到 snapshot，列表不再挂旧分数
        snapshot["last_review"] = plan.review
        plan.review = None
    if snapshot:
        plan.basis_snapshot = snapshot

    return {
        "archived_count": len(others),
        "archived_titles": [p.title for p in others][:5],
        "reactivated": was_archived,
        "reactivated_at": reactivated_at,
    }


def shift_to_anchor(db: Session, plan: DietPlan, anchor: date | None = None) -> dict:
    """方案日期整体顺延，让第 1 天落在 anchor（默认今天）。

    只对"已过期"的方案动手；未来或当天的方案原样返回，避免把用户的排期打乱。
    """
    anchor = anchor or date.today()
    days = (db.query(PlanDay).filter(PlanDay.plan_id == plan.id)
            .order_by(PlanDay.day_index.asc()).all())
    dated = [d for d in days if d.date]
    if not dated:
        return {"shift_days": 0, "from_date": None, "to_date": None}

    first = min(d.date for d in dated)
    delta = (anchor - first).days
    if delta <= 0:
        return {"shift_days": 0, "from_date": str(first), "to_date": str(first)}

    for d in dated:
        d.date = d.date + timedelta(days=delta)
    if plan.start_date:
        plan.start_date = plan.start_date + timedelta(days=delta)
    # 尚未打卡的任务跟着平移，否则会留下一批"幽灵任务"挂在旧日期上（重复计入完成率）
    pending = (db.query(CheckIn)
               .filter(CheckIn.plan_day_id.in_([d.id for d in dated]),
                       CheckIn.status == "pending")
               .all())
    for task in pending:
        if task.check_date:
            task.check_date = task.check_date + timedelta(days=delta)
    snapshot = dict(plan.basis_snapshot or {})
    snapshot.setdefault("original_start_date", str(first))
    plan.basis_snapshot = snapshot
    logger.info("方案 %s 日期过期，已顺延 %d 天到今天", plan.id, delta)
    return {"shift_days": delta, "from_date": str(first), "to_date": str(anchor)}


def _main_exercise(day: PlanDay) -> dict:
    exercises = day.exercises or ([day.exercise] if day.exercise else [])
    return dict(exercises[0]) if exercises else {}


def _burn_per_min(plan: DietPlan, day: PlanDay) -> float:
    profile_ctx = plan.basis_snapshot or {}
    try:
        weight = float(profile_ctx.get("weight_kg") or 65)
    except (TypeError, ValueError):
        weight = 65.0
    main = _main_exercise(day)
    met = plan_targets._met_for(main.get("type") or "快走", main.get("intensity") or "moderate")
    return max(met * weight / 60.0, 1.0), main


def suggest_exercise(plan: DietPlan, day: PlanDay, excess_kcal: float) -> dict:
    """只算不改：把超出热量折算成几分钟（用于前端按钮文案）"""
    if excess_kcal <= 0:
        return {"added_min": 0}
    per_min, main = _burn_per_min(plan, day)
    added_min = int(min(60, max(5, round((excess_kcal / per_min) / 5.0) * 5)))
    return {
        "added_min": added_min,
        "type": main.get("type") or "快走",
        "calories_per_min": round(per_min, 1),
    }


def rebalance_by_exercise(plan: DietPlan, day: PlanDay, excess_kcal: float) -> dict:
    """把超出上限的热量折算成当日运动时长（加在主项上），返回追加的明细"""
    exercises = [dict(item) for item in (day.exercises or ([day.exercise] if day.exercise else []))]
    if not exercises or excess_kcal <= 0:
        return {"added_min": 0}

    per_min, _ = _burn_per_min(plan, day)
    added_min = int(min(60, max(5, round((excess_kcal / per_min) / 5.0) * 5)))

    main = exercises[0]
    duration = int(main.get("duration_min") or 0) + added_min
    main["duration_min"] = duration
    main["calories_burned"] = int(round(per_min * duration))
    main["tips"] = (main.get("tips") or "") + f"；因今日餐食超上限，追加 {added_min} 分钟"

    exercises[0] = main
    day.exercises = exercises
    day.exercise = main
    return {"added_min": added_min, "duration_min": duration,
            "calories_burned": main["calories_burned"], "type": main.get("type")}
