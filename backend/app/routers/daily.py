"""每日汇总：一次请求拿全「今天该多少 / 已经多少」

日程顶部的目标条、我的页的当日数据卡都读这个接口，
避免前端分别请求目标、饮食、打卡、饮水、睡眠再自己拼，数字对不上。
"""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.check_in import CheckIn
from app.models.diet_plan import DietPlan
from app.models.food_record import FoodRecord
from app.models.user import User
from app.services import day_context, wellness

router = APIRouter(prefix="/day", tags=["每日汇总"])


def _pct(value: float, target: float | None) -> int | None:
    if not target:
        return None
    return int(round(min(max(value / target, 0), 2) * 100))


@router.get("/summary")
def day_summary(
    date_str: str | None = Query(default=None, alias="date"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    day = date.fromisoformat(date_str) if date_str else date.today()
    targets = day_context.targets_for_day(db, user.id, day)
    plan_day = day_context.active_plan_day(db, user.id, day)
    plan = db.get(DietPlan, plan_day.plan_id) if plan_day else None

    records = (db.query(FoodRecord)
               .filter(FoodRecord.user_id == user.id, FoodRecord.eaten_date == day).all())
    intake = {
        "calories": round(sum(r.calories or 0 for r in records), 1),
        "protein_g": round(sum(r.protein_g or 0 for r in records), 1),
        "fat_g": round(sum(r.fat_g or 0 for r in records), 1),
        "carb_g": round(sum(r.carb_g or 0 for r in records), 1),
        "records": len(records),
    }

    tasks = (db.query(CheckIn)
             .filter(CheckIn.user_id == user.id, CheckIn.check_date == day).all())
    done_tasks = [c for c in tasks if c.status == "done"]
    exercise_tasks = [c for c in done_tasks if c.check_type == "exercise"]
    burn = {
        "exercise_kcal": round(sum(c.calories_burned or 0 for c in exercise_tasks)),
        "exercise_min": sum(c.duration_min or 0 for c in exercise_tasks),
        "times": len(exercise_tasks),
    }

    total_water = wellness.water_total(db, user.id, day)
    water_target = int(targets.get("water_min_ml") or 2000)
    sleep = wellness.sleep_dict(wellness.day_sleep(db, user.id, day))

    calorie_min = targets.get("calorie_min")
    calorie_max = targets.get("calorie_max")
    calorie_status = None
    if calorie_max:
        if intake["calories"] > calorie_max:
            calorie_status = "over"
        elif calorie_min and intake["calories"] >= calorie_min:
            calorie_status = "in_range"
        else:
            calorie_status = "under"

    return {
        "date": str(day),
        "has_plan": plan_day is not None,
        "plan": {
            "id": plan.id if plan else None,
            "title": plan.title if plan else None,
            "day_index": plan_day.day_index if plan_day else None,
            "theme": plan_day.theme if plan_day else None,
            "meals": plan_day.meals if plan_day else [],
            "exercises": (plan_day.exercises or ([plan_day.exercise] if plan_day.exercise else []))
            if plan_day else [],
            "sleep": plan_day.sleep if plan_day else None,
            "water_ml": plan_day.water_ml if plan_day else None,
        },
        "targets": targets,
        "intake": {**intake, "calorie_status": calorie_status},
        "burn": burn,
        "water": {
            "total_ml": total_water,
            "target_ml": water_target,
            "pct": _pct(total_water, water_target),
            "achieved": total_water >= water_target,
        },
        "sleep": sleep,
        "tasks": {
            "total": len(tasks),
            "done": len(done_tasks),
            "pct": _pct(len(done_tasks), len(tasks)) if tasks else 0,
        },
        "progress": {
            "calorie_pct": _pct(intake["calories"], targets.get("calorie_target") or calorie_max),
            "protein_pct": _pct(intake["protein_g"], targets.get("protein_min_g")),
            "burn_pct": _pct(burn["exercise_kcal"], targets.get("exercise_burn_min")),
        },
    }
