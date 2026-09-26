"""打卡路由：日历任务体系（按完成度给积分的核心）

流程：方案天 --from-plan 导入--> 日历上出现 pending 任务 --> 逐项 complete 变 done
     每完成一项 +3 分；当天任务全部完成额外 +10 分
另支持手动运动/饮食打卡（每天各首次 +2 分）
"""
import re
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.check_in import CheckIn
from app.models.diet_plan import DietPlan, PlanDay
from app.models.user import User
from app.schemas.checkin import FromPlanIn, ManualCheckInIn
from app.services import points

router = APIRouter(prefix="/check-ins", tags=["健康打卡"])

TASK_LABELS = {
    "meal_breakfast": "早餐", "meal_lunch": "午餐",
    "meal_dinner": "晚餐", "meal_snack": "加餐", "exercise": "运动",
}


def checkin_dict(c: CheckIn, plan_day: PlanDay | None = None) -> dict:
    """打卡/任务序列化；pending 任务附带方案里的餐食详情，方便前端展示"要吃什么\""""
    detail = None
    if plan_day and c.task_key:
        if c.task_key.startswith("meal_"):
            meal_type = c.task_key.replace("meal_", "")
            detail = next((m for m in (plan_day.meals or []) if m.get("meal_type") == meal_type), None)
        elif c.task_key == "exercise":
            detail = plan_day.exercise
    return {
        "id": c.id, "check_date": str(c.check_date), "check_type": c.check_type,
        "task_key": c.task_key, "label": TASK_LABELS.get(c.task_key, "手动记录"),
        "status": c.status, "points_awarded": c.points_awarded,
        "exercise_type": c.exercise_type, "duration_min": c.duration_min,
        "calories_burned": c.calories_burned, "note": c.note,
        "plan_day_id": c.plan_day_id, "detail": detail,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("")
def list_checkins(
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD 查单日"),
    month: str | None = Query(default=None, description="YYYY-MM 查月历打点"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查单日任务列表 或 月历完成度打点（二选一）"""
    if month:
        if not re.match(r"^\d{4}-\d{2}$", month):
            raise HTTPException(status_code=400, detail="month 格式应为 YYYY-MM")
        year, mon = int(month[:4]), int(month[5:7])
        start = date(year, mon, 1)
        end = date(year + (mon == 12), mon % 12 + 1, 1)
        rows = (db.query(CheckIn)
                .filter(CheckIn.user_id == user.id, CheckIn.check_date >= start, CheckIn.check_date < end)
                .all())
        by_date: dict[str, dict] = {}
        for c in rows:
            key = str(c.check_date)
            stat = by_date.setdefault(key, {"date": key, "total": 0, "done": 0})
            stat["total"] += 1
            stat["done"] += 1 if c.status == "done" else 0
        return {"month": month, "days": sorted(by_date.values(), key=lambda d: d["date"])}

    # 单日视图
    day = date.fromisoformat(date_str) if date_str else date.today()
    rows = (db.query(CheckIn)
            .filter(CheckIn.user_id == user.id, CheckIn.check_date == day)
            .order_by(CheckIn.created_at.asc()).all())
    # 批量取关联的方案天（展示待完成任务详情）
    plan_day_ids = {c.plan_day_id for c in rows if c.plan_day_id}
    plan_days = {d.id: d for d in db.query(PlanDay).filter(PlanDay.id.in_(plan_day_ids))} if plan_day_ids else {}
    done = sum(1 for c in rows if c.status == "done")
    return {
        "date": str(day),
        "items": [checkin_dict(c, plan_days.get(c.plan_day_id)) for c in rows],
        "summary": {"total": len(rows), "done": done,
                    "all_done": bool(rows) and done == len(rows)},
    }


@router.post("/from-plan")
def import_from_plan(
    data: FromPlanIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """把方案某天导入日历：为每餐+运动生成待完成任务（幂等，重复导入自动跳过）"""
    plan_day = db.get(PlanDay, data.plan_day_id)
    if not plan_day:
        raise HTTPException(status_code=404, detail="方案天不存在")
    plan = db.get(DietPlan, plan_day.plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="方案不存在")

    created, skipped = [], []
    # 餐食任务
    for meal in plan_day.meals or []:
        task_key = f"meal_{meal.get('meal_type', 'snack')}"
        exists = (db.query(CheckIn)
                  .filter(CheckIn.user_id == user.id, CheckIn.plan_day_id == plan_day.id,
                          CheckIn.task_key == task_key).first())
        if exists:
            skipped.append(task_key)
            continue
        c = CheckIn(user_id=user.id, check_date=plan_day.date or date.today(),
                    check_type="diet", task_key=task_key, status="pending",
                    plan_day_id=plan_day.id)
        db.add(c)
        created.append(task_key)
    # 运动任务
    if plan_day.exercise:
        exists = (db.query(CheckIn)
                  .filter(CheckIn.user_id == user.id, CheckIn.plan_day_id == plan_day.id,
                          CheckIn.task_key == "exercise").first())
        if exists:
            skipped.append("exercise")
        else:
            db.add(CheckIn(user_id=user.id, check_date=plan_day.date or date.today(),
                           check_type="exercise", task_key="exercise", status="pending",
                           plan_day_id=plan_day.id))
            created.append("exercise")

    db.commit()
    return {"created": created, "skipped": skipped,
            "message": f"已导入 {len(created)} 项任务到日历" if created else "该天任务已在日历中"}


@router.post("/{checkin_id}/complete")
def complete_checkin(
    checkin_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """完成任务打卡：pending → done，按完成度给分（单项+3，当日全清额外+10）"""
    c = db.get(CheckIn, checkin_id)
    if not c or c.user_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    if c.status == "done":
        raise HTTPException(status_code=400, detail="该任务已完成")

    c.status = "done"
    # 注意：session 配置了 autoflush=False，先 flush 让下面的 pending 统计能看到本次改动
    db.flush()
    action = "task_exercise" if c.check_type == "exercise" else "task_meal"
    label = TASK_LABELS.get(c.task_key, "任务")
    pts = points.award(db, user, action, description=f"完成方案任务：{label}",
                       ref_type="checkin", ref_id=c.id) or 0
    c.points_awarded = pts

    # 当日任务全部完成 → 额外奖励 +10（每天只发一次）
    bonus = 0
    remaining = (db.query(CheckIn)
                 .filter(CheckIn.user_id == user.id, CheckIn.check_date == c.check_date,
                         CheckIn.status == "pending").count())
    if remaining == 0:
        bonus = points.award(db, user, "task_all_done_bonus",
                             description=f"{c.check_date} 方案任务全部完成 🎉",
                             once_per_day=True) or 0
    db.commit()
    return {"id": c.id, "status": c.status,
            "points_awarded": pts + bonus, "bonus": bonus,
            "message": f"任务完成 +{pts} 分" + (f"，当日全部完成 +{bonus} 分 🎉" if bonus else "")}


@router.post("")
def manual_checkin(
    data: ManualCheckInIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """手动打卡：运动 / 补记饮食（每天每类首次 +2 分）"""
    day = date.fromisoformat(data.check_date) if data.check_date else date.today()
    c = CheckIn(
        user_id=user.id, check_date=day, check_type=data.check_type,
        task_key="manual_food" if data.check_type == "diet" else "manual_exercise",
        status="done",
        exercise_type=data.exercise_type, duration_min=data.duration_min,
        calories_burned=data.calories_burned, note=data.note,
    )
    db.add(c)
    ref = data.check_type   # 用 ref_type 区分饮食/运动的每日首次
    desc = ("手动记录饮食" if data.check_type == "diet"
            else f"手动运动打卡：{(data.exercise_type or '')}{(str(data.duration_min or '') + '分钟')}".strip())
    pts = points.award(db, user, "manual_checkin", once_per_day=True,
                       description=desc, ref_type=ref) or 0
    c.points_awarded = pts
    db.commit()
    db.refresh(c)
    return {**checkin_dict(c), "points_awarded": pts}


@router.delete("/{checkin_id}")
def delete_checkin(checkin_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """删除打卡/任务（积分不回收）"""
    c = db.get(CheckIn, checkin_id)
    if not c or c.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    db.delete(c)
    db.commit()
    return {"ok": True}
