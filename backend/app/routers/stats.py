"""统计路由：仪表盘总览 + 图表数据（热量趋势/体重轨迹/营养结构）"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.check_in import CheckIn
from app.models.health_profile import HealthProfile
from app.models.user import User
from app.models.weight_log import WeightLog
from app.models.device import DeviceMetric
from app.services import diet_intake
from app.services.nutrition import calc_bmr

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
    # 今日热量摄入：与日程页同一口径（记了用记录，没记的按方案打卡折算），
    # 否则会出现"日程 460、概览 0"的矛盾
    today_cal = diet_intake.day_intake(db, user.id, today)["calories"]
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
    # 近 7 天：日均摄入（同一口径，打卡过但没记食物的一天也算进当天）
    week_start = today - timedelta(days=6)
    week_sum = sum(diet_intake.day_intake(db, user.id, week_start + timedelta(days=i))["calories"]
                   for i in range(7))
    week_avg_intake = round(week_sum / 7, 1)
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
    result = []
    for i in range(days):
        d = date.today() - timedelta(days=days - 1 - i)
        result.append({"date": str(d),
                       "calories": diet_intake.day_intake(db, user.id, d)["calories"]})
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
    # 口径与日程/概览一致：记了用记录，没记但打卡过的餐按方案折算；
    # 分母只算"这天有数据"，避免空白天把日均摊薄
    total_p = total_f = total_c = 0.0
    n = 0
    for i in range(days):
        intake = diet_intake.day_intake(db, user.id, start + timedelta(days=i))
        if intake["calories"] > 0 or intake["protein_g"] > 0:
            n += 1
            total_p += intake["protein_g"]
            total_f += intake["fat_g"]
            total_c += intake["carb_g"]
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

    # 摄入四项用统一下来的口径（记录 + 已打卡餐次的方案折算），
    # 否则趋势图会和日程页对不上
    dates_list = [today - timedelta(days=days - 1 - i) for i in range(days)]
    intake_by_date = {d: diet_intake.day_intake(db, user.id, d) for d in dates_list}
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
    for d in dates_list:
        intake = intake_by_date[d]
        points.append({
            "date": str(d),
            "calories": round(intake["calories"], 1),
            "target": target,
            "protein_g": round(intake["protein_g"], 1),
            "fat_g": round(intake["fat_g"], 1),
            "carb_g": round(intake["carb_g"], 1),
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
def weekly_report(refresh: bool = Query(default=False, description="true=忽略缓存重新生成"),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """AI 健康周报：汇总近 7 天数据 → 大模型点评与建议（失败自动降级规则版）

    结果按「用户 + 统计窗口」缓存，同一天内重复打开不再调模型；
    需要重新生成（比如刚补完记录）传 ?refresh=true。
    """
    from app.services.weekly_report import collect_week_stats, generate_weekly_report

    report = generate_weekly_report(db, user, force=refresh)
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


# ============ 能量天平（融合自 feat/personalized-burn，适配主分支数据口径） ============
# 摄入：直接复用主分支 diet_intake.day_intake（食物记录 + 已打卡未记餐次的方案折算），
#       保证与「我的 / 日程」页显示的摄入数字完全一致。
# 总消耗 = 基础代谢 BMR + 手动运动打卡消耗 + 手环记录的活动消耗。
#       后两项分别返回，前端分两行展示，用户能直观看到「自己打卡的」和「手环记录的」。
# 数据来源都在同一个 foodai.db、同一个 user_id、同一天 → 两边数据天然互通。
@router.get("/energy")
def energy_balance(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    today = date.today()
    intake = diet_intake.day_intake(db, user.id, today)
    intake_kcal = round(float(intake.get("calories") or 0))
    records = int(intake.get("records") or 0)

    # 1) 手动在系统里打卡的运动消耗（主分支 check_ins.calories_burned）
    done_ex = (db.query(CheckIn)
               .filter(CheckIn.user_id == user.id, CheckIn.check_date == today,
                       CheckIn.check_type == "exercise", CheckIn.status == "done").all())
    checkin_burn = round(sum(float(c.calories_burned or 0) for c in done_ex))

    # 2) 手环记录的活动消耗（我新增的 device_metrics，与主分支同库同用户同日）
    band_rows = (db.query(DeviceMetric)
                 .filter(DeviceMetric.user_id == user.id,
                         DeviceMetric.metric_date == today).all())
    band_burn = round(sum(float(m.calories or 0) for m in band_rows))
    band_steps = sum(int(m.steps or 0) for m in band_rows)
    band_sleep = max((int(m.sleep_minutes or 0) for m in band_rows), default=0)
    band_source = band_rows[0].source if band_rows else None

    # 3) 基础代谢：按用户档案用 Mifflin-St Jeor 算，档案不全时给温和兜底值
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    bmr = None
    if profile:
        bmr = calc_bmr(profile.weight_kg, profile.height_cm, profile.age, profile.gender)
    bmr = int(bmr or 1500)

    activity = checkin_burn + band_burn
    total_burn = bmr + activity
    gap = intake_kcal - total_burn
    target = int(profile.daily_calorie_target) if profile and profile.daily_calorie_target else None

    has_intake = records > 0 or intake_kcal > 0
    if not has_intake:
        verdict = "今天还没有饮食记录，先去记一餐吧"
    elif gap > 300:
        verdict = f"摄入比消耗多 {gap} kcal，今天略有盈余"
    elif gap < -300:
        verdict = f"消耗比摄入多 {-gap} kcal，今天处于热量缺口"
    else:
        verdict = "摄入与消耗基本持平，保持得不错"

    return {
        "date": str(today),
        "intake": {"value": intake_kcal, "target": target, "unit": "kcal"},
        "burn": {
            "total": total_burn,
            "bmr": bmr,
            "checkin": checkin_burn,
            "band": band_burn,
            "activity": activity,
            "unit": "kcal",
        },
        "gap": gap,
        "verdict": verdict,
        "has_intake": has_intake,
        "has_checkin": checkin_burn > 0,
        "has_device": bool(band_rows),
        "band": {
            "source": band_source,
            "steps": band_steps,
            "sleep_h": round(band_sleep / 60, 1) if band_sleep else None,
        },
    }
