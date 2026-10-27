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

    # 今日运动消耗
    today_burn = (db.query(func.sum(CheckIn.calories_burned))
                  .filter(CheckIn.user_id == user.id, CheckIn.check_type == "exercise",
                          CheckIn.status == "done", CheckIn.check_date == today).scalar())
    # 近 7 天：日均摄入 / 总消耗
    week_start = today - timedelta(days=6)
    week_avg_intake = round((db.query(func.coalesce(func.sum(FoodRecord.calories), 0.0))
                .filter(FoodRecord.user_id == user.id, FoodRecord.eaten_date >= week_start).scalar() or 0) / 7, 1)
    week_burn = (db.query(func.coalesce(func.sum(CheckIn.calories_burned), 0.0))
                 .filter(CheckIn.user_id == user.id, CheckIn.check_type == "exercise",
                         CheckIn.status == "done", CheckIn.check_date >= week_start).scalar())

    return {
        "today_calories": round(today_cal or 0, 1),
        "daily_target": profile.daily_calorie_target if profile else None,
        "streak_days": calc_streak(db, user.id),
        "points_total": user.points_total,
        "rank": (higher or 0) + 1,
        "today_burn": round(today_burn or 0, 1),
        "week_avg_intake": week_avg_intake,
        "week_burn": week_burn,
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
    # 先按天求和，再除以"有记录的天数"：避免某天记了 4 餐就被加权 4 次，
    # 也避免把没记录的空白天算进分母（否则 30 天口径会把日均摊薄到 1/4）
    row = (db.query(func.coalesce(func.sum(FoodRecord.protein_g), 0.0),
                    func.coalesce(func.sum(FoodRecord.fat_g), 0.0),
                    func.coalesce(func.sum(FoodRecord.carb_g), 0.0),
                    func.count(func.distinct(FoodRecord.eaten_date)))
           .filter(FoodRecord.user_id == user.id, FoodRecord.eaten_date >= start).first())
    total_p, total_f, total_c, n_days = row
    n = n_days or 0
    return {
        "days_with_data": n,
        "avg_protein_g": round(total_p / n, 1) if n else 0.0,
        "avg_fat_g": round(total_f / n, 1) if n else 0.0,
        "avg_carb_g": round(total_c / n, 1) if n else 0.0,
    }


@router.get("/trends")
def trends(period: str = Query(default="week", alias="range", pattern="^(week|month)$"),
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """数据追踪页统一聚合接口：近 7 天 / 近 30 天，逐日返回四条曲线的数据

    - calories：当日摄入；target：每日热量目标（对比线，逐日重复方便前端直接画）
    - protein/fat/carb：当日营养素摄入（g）
    - burn：当日运动消耗（来自打卡）
    - weight：当日体重（多条取日均值，无记录为 null）
    """
    days = 7 if period == "week" else 30
    today = date.today()
    start = today - timedelta(days=days - 1)

    def daily_sum(model_col, model_date_col, extra=None):
        q = (db.query(model_date_col, func.sum(model_col))
             .filter(model_date_col >= start, model_date_col <= today))
        if extra is not None:
            q = q.filter(extra)
        return {d: v or 0 for d, v in q.group_by(model_date_col).all()}

    cal = daily_sum(FoodRecord.calories, FoodRecord.eaten_date,
                    FoodRecord.user_id == user.id)
    protein = daily_sum(FoodRecord.protein_g, FoodRecord.eaten_date,
                        FoodRecord.user_id == user.id)
    fat = daily_sum(FoodRecord.fat_g, FoodRecord.eaten_date,
                    FoodRecord.user_id == user.id)
    carb = daily_sum(FoodRecord.carb_g, FoodRecord.eaten_date,
                     FoodRecord.user_id == user.id)
    burn = daily_sum(CheckIn.calories_burned, CheckIn.check_date,
                     (CheckIn.user_id == user.id) & (CheckIn.check_type == "exercise")
                     & (CheckIn.status == "done"))

    # 体重：一天多次记录取当日均值
    w_rows = (db.query(WeightLog.recorded_date, func.avg(WeightLog.weight_kg))
              .filter(WeightLog.user_id == user.id,
                      WeightLog.recorded_date >= start, WeightLog.recorded_date <= today)
              .group_by(WeightLog.recorded_date).all())
    weight = {d: round(v, 1) for d, v in w_rows}

    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    target = profile.daily_calorie_target if profile else None

    points = []
    for i in range(days):
        d = today - timedelta(days=days - 1 - i)
        points.append({
            "date": str(d),
            "calories": round(cal.get(d, 0), 1),
            "target": target,
            "protein_g": round(protein.get(d, 0), 1),
            "fat_g": round(fat.get(d, 0), 1),
            "carb_g": round(carb.get(d, 0), 1),
            "burn": round(burn.get(d, 0), 1),
            "weight_kg": weight.get(d),
        })

    recorded_days = sum(1 for p in points if p["calories"] > 0)
    return {
        "range": period,
        "days": days,
        "daily_target": target,
        "recorded_days": recorded_days,
        "points": points,
    }


@router.get("/weekly-report")
def weekly_report(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """AI 健康周报：汇总近 7 天数据 → 大模型点评与建议（失败自动降级规则版）"""
    from app.services.weekly_report import collect_week_stats, generate_weekly_report

    report = generate_weekly_report(db, user)
    s = collect_week_stats(db, user)   # 前端展示原始数据卡片用
    return {
        "summary": report.summary,
        "score": report.score,
        "highlights": report.highlights,
        "suggestions": report.suggestions,
        "source": report.source,
        "week_stats": {
            "start": str(s["start"]), "end": str(s["end"]),
            "days_with_records": s["days_with_records"],
            "avg_intake": s["avg_intake"], "target": s["target"],
            "total_burn": s["total_burn"], "exercise_times": s["exercise_times"],
            "checkin_days": s["checkin_days"], "streak_days": s["streak_days"],
            "weight_change": s["weight_change"],
        },
    }
