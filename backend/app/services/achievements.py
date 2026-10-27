"""成就徽章解锁服务

设计：
- 徽章/头衔按「条件类型 + 阈值」自动解锁（连续打卡、累计识别/保存/运动、积分、方案数）
- unlock_all()：重算用户各项指标，把达标且尚未解锁的成就批量解锁（幂等）
- 业务动作完成后调用 unlock_all() 即可（如打卡、识别、保存、生成方案、积分变动）
"""
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.achievement import Achievement, UserAchievement
from app.models.check_in import CheckIn
from app.models.diet_plan import DietPlan
from app.models.food_record import FoodRecord
from app.models.recognition import Recognition
from app.models.user import User


def calc_streak(db: Session, user_id: int) -> int:
    """连续打卡天数：从今天（或昨天）往前数连续有 done 打卡的天数"""
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


def compute_metrics(db: Session, user: User) -> dict[str, int]:
    """计算用户的各项成就指标"""
    return {
        "recognize_count": db.query(Recognition).filter(Recognition.user_id == user.id).count(),
        "save_record_count": db.query(FoodRecord).filter(FoodRecord.user_id == user.id).count(),
        "streak_days": calc_streak(db, user.id),
        "manual_exercise_count": db.query(CheckIn).filter(
            CheckIn.user_id == user.id,
            CheckIn.task_key == "manual_exercise",
            CheckIn.status == "done").count(),
        "points_total": user.points_total or 0,
        "plan_count": db.query(DietPlan).filter(DietPlan.user_id == user.id).count(),
    }


def unlock_all(db: Session, user: User) -> list[Achievement]:
    """解锁所有达标的成就，返回本次新解锁的成就列表（不 commit，由路由层统一提交）"""
    metrics = compute_metrics(db, user)
    unlocked: list[Achievement] = []
    owned = {ua.achievement_id for ua in db.query(UserAchievement).filter(
        UserAchievement.user_id == user.id).all()}
    for a in db.query(Achievement).all():
        if a.id in owned:
            continue
        if metrics.get(a.condition_type, 0) >= a.threshold:
            db.add(UserAchievement(user_id=user.id, achievement_id=a.id))
            unlocked.append(a)
    return unlocked


def achievement_dict(a: Achievement, unlocked: bool, equipped: bool, unlocked_at=None) -> dict:
    """成就序列化"""
    return {
        "id": a.id, "code": a.code, "name": a.name, "icon": a.icon,
        "description": a.description, "category": a.category,
        "condition_type": a.condition_type, "threshold": a.threshold,
        "unlocked": unlocked, "equipped": equipped,
        "unlocked_at": unlocked_at.isoformat() if unlocked_at else None,
    }
