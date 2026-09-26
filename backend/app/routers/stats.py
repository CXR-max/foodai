"""统计路由：仪表盘总览 + 图表数据（热量趋势/体重轨迹/营养结构）"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.check_in import CheckIn
from app.models.food_record import FoodRecord
from app.models.health_profile import HealthProfile
from app.models.user import User
from app.models.weight_log import WeightLog

router = APIRouter(prefix="/stats", tags=["数据统计"])


def calc_streak(db: Session, user_id: int) -> int:
    """连续打卡天数：从今天（或昨天）往前数连续有已完成打卡的天数"""
    dates = [row[0] for row in db.query(CheckIn.check_date).filter(
        CheckIn.user_id == user_id, CheckIn.status == "done").distinct().all()]
    if not dates:
        return 0
    done_days = set(dates)
    today = date.today()
    start = today if today in done_days else today - timedelta(days=1)
    streak = 0
    d = start
    while d in done_days:
        streak += 1
        d -= timedelta(days=1)
    return streak


@router.get("/overview")
def overview(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """仪表盘一屏数据：今日热量/目标、连续打卡、积分、排名"""
    today = date.today()
    # 今日热量摄入
    today_cal = (db.query(func.coalesce(func.sum(FoodRecord.calories), 0.0))
                 .filter(FoodRecord.user_id == user.id, FoodRecord.eaten_date == today).scalar())
    # 档案里的热量目标
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    # 今日打卡情况
    today_rows = db.query(CheckIn).filter(CheckIn.user_id == user.id, CheckIn.check_date == today).all()
    # 排名：积分比我高的人数 + 1
    higher = db.query(func.count(User.id)).filter(User.points_total > user.points_total).scalar()

    return {
        "today_calories": round(today_cal or 0, 1),
        "daily_target": profile.daily_calorie_target if profile else None,
        "streak_days": calc_streak(db, user.id),
        "points_total": user.points_total,
        "rank": (higher or 0) + 1,
        "today_tasks": {
            "total": len(today_rows),
            "done": sum(1 for c in today_rows if c.status == "done"),
        },
        "today_checked": {
            "diet": any(c.check_type == "diet" and c.status == "done" for c in today_rows),
            "exercise": any(c.check_type == "exercise" and c.status == "done" for c in today_rows),
        },
    }


@router.get("/calories")
def calories_trend(days: int = Query(default=14, ge=1, le=90),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """近 N 天每日热量摄入（没记录的天补 0，方便前端画连续柱状图）"""
    rows = (db.query(FoodRecord.eaten_date, func.sum(FoodRecord.calories))
            .filter(FoodRecord.user_id == user.id,
                    FoodRecord.eaten_date >= date.today() - timedelta(days=days - 1))
            .group_by(FoodRecord.eaten_date).all())
    by_date = {d: round(v or 0, 1) for d, v in rows}
    result = []
    for i in range(days):
        d = date.today() - timedelta(days=days - 1 - i)
        result.append({"date": str(d), "calories": by_date.get(d, 0)})
    return result


@router.get("/weight")
def weight_trend(days: int = Query(default=90, ge=1, le=365),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """体重轨迹（升序）"""
    start = date.today() - timedelta(days=days - 1)
    logs = (db.query(WeightLog)
            .filter(WeightLog.user_id == user.id, WeightLog.recorded_date >= start)
            .order_by(WeightLog.recorded_date.asc()).all())
    return [{"date": str(w.recorded_date), "weight_kg": w.weight_kg} for w in logs]


@router.get("/nutrition")
def nutrition_summary(days: int = Query(default=7, ge=1, le=30),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """近 N 天营养结构均值（蛋白质/脂肪/碳水日均摄入，画饼图）"""
    start = date.today() - timedelta(days=days - 1)
    row = (db.query(func.avg(FoodRecord.protein_g), func.avg(FoodRecord.fat_g),
                    func.avg(FoodRecord.carb_g), func.count(func.distinct(FoodRecord.eaten_date)))
           .filter(FoodRecord.user_id == user.id, FoodRecord.eaten_date >= start)).first()
    avg_p, avg_f, avg_c, n_days = row
    # avg 是"有记录的天"的日均值，乘以天数占比换算成"全周期日均"
    factor = (n_days or 0) / days
    return {
        "days_with_data": n_days or 0,
        "avg_protein_g": round((avg_p or 0) * factor, 1),
        "avg_fat_g": round((avg_f or 0) * factor, 1),
        "avg_carb_g": round((avg_c or 0) * factor, 1),
    }
