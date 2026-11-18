"""把方案天导入日程（日历任务）

单天导入（原「导入日历」）与整周导入共用同一套覆盖规则：
- 同一 (日期 + 任务) 已打卡(done) → 保留不动，绝不删除
- 同一 (日期 + 任务) 未打卡(pending) → 覆盖为本次方案内容（指向新方案天）
- 该日期上、方案里已不存在的旧 pending 方案任务 → 删除；done 保留

任务种类：四餐 / 运动 / 睡眠 / 饮水（后两类是本版新增，来自 plan_days.sleep / water_ml）
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.check_in import CheckIn
from app.models.diet_plan import PlanDay


def desired_tasks(plan_day: PlanDay) -> dict[str, str]:
    """方案天 → {task_key: check_type}"""
    desired: dict[str, str] = {}
    for meal in plan_day.meals or []:
        desired[f"meal_{meal.get('meal_type', 'snack')}"] = "diet"
    if plan_day.exercises or plan_day.exercise:
        desired["exercise"] = "exercise"
    if plan_day.sleep:
        desired["sleep"] = "sleep"
    if plan_day.water_ml:
        desired["water"] = "water"
    return desired


def import_plan_days(db: Session, user_id: int, plan_days: list[PlanDay]) -> dict:
    """把若干方案天写入日程任务；返回新增/更新/保留/清理的明细。"""
    created: list[str] = []
    updated: list[str] = []
    kept: list[str] = []
    removed = 0

    for plan_day in plan_days:
        day = plan_day.date
        if day is None:
            continue
        desired = desired_tasks(plan_day)
        # 先清掉挂在同一天方案上、但日期已经不符的待办（方案顺延过就会留下这种幽灵任务）
        stale = (db.query(CheckIn)
                 .filter(CheckIn.user_id == user_id, CheckIn.plan_day_id == plan_day.id,
                         CheckIn.status == "pending")
                 .all())
        for task in stale:
            if task.check_date != day:
                db.delete(task)
                removed += 1

        existing = (db.query(CheckIn)
                    .filter(CheckIn.user_id == user_id, CheckIn.check_date == day).all())
        by_key = {c.task_key: c for c in existing if c.task_key}

        for key, ctype in desired.items():
            task = by_key.get(key)
            if task is None:
                db.add(CheckIn(user_id=user_id, check_date=day, check_type=ctype,
                               task_key=key, status="pending", plan_day_id=plan_day.id))
                created.append(key)
            elif task.status == "done":
                kept.append(key)
            else:
                task.plan_day_id = plan_day.id
                task.check_type = ctype
                updated.append(key)

        for key, task in by_key.items():
            if key not in desired and task.status == "pending" and task.plan_day_id:
                db.delete(task)
                removed += 1

    db.commit()
    return {"created": created, "updated": updated, "kept": kept, "removed": removed}
