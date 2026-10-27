"""
积分规则引擎（★★★ 想改积分数值，改下面 RULES 这一张表就行 ★★★）

设计：
- award()：加积分 = 写流水 + 同步累加 users.points_total（排行榜数据源）
- spend()：扣积分（兑换奖励），余额不足返回 False
- 防刷：once_per_day / once_ever 两类去重，都是查 points_logs 流水
"""
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models.points_log import PointsLog
from app.models.user import User

# 积分规则表：action -> (说明文案, 分值)
RULES = {
    "complete_profile":     ("完善健康档案", 20),
    "save_recognition":     ("保存识别记录", 2),
    "generate_plan":        ("生成养生方案", 10),
    "log_weight":           ("记录体重", 2),
    "task_meal":            ("完成方案餐食任务", 3),
    "task_exercise":        ("完成方案运动任务", 3),
    "task_all_done_bonus":  ("当日方案任务全部完成", 10),
    "manual_checkin":       ("手动打卡", 2),
    "redeem_reward":        ("兑换奖励", 0),   # 兑换是负数，由 spend() 传入
}


def awarded_today(db: Session, user_id: int, action: str, ref_type: str | None = None) -> bool:
    """今天是否已拿过该积分（ref_type 可进一步区分，如手动打卡的 diet/exercise）"""
    start = datetime.combine(date.today(), datetime.min.time())
    q = db.query(PointsLog).filter(
        PointsLog.user_id == user_id,
        PointsLog.action == action,
        PointsLog.created_at >= start,
        PointsLog.points > 0,
    )
    if ref_type:
        q = q.filter(PointsLog.ref_type == ref_type)
    return db.query(q.exists()).scalar()


def awarded_ever(db: Session, user_id: int, action: str) -> bool:
    """历史上是否拿过该积分（如完善档案只奖一次）"""
    return db.query(
        db.query(PointsLog)
        .filter(PointsLog.user_id == user_id, PointsLog.action == action, PointsLog.points > 0)
        .exists()
    ).scalar()


def award(
    db: Session,
    user: User,
    action: str,
    description: str = "",
    points: int | None = None,
    ref_type: str | None = None,
    ref_id: int | None = None,
    once_per_day: bool = False,
    once_ever: bool = False,
) -> int | None:
    """
    给用户加积分。
    返回实得积分；因防刷跳过时返回 None（调用方据此给前端提示）。
    注意：只 add 不 commit —— 由路由层统一 commit，保证与业务数据同事务。
    """
    desc_default, default_points = RULES[action]
    if once_per_day and awarded_today(db, user.id, action, ref_type):
        return None
    if once_ever and awarded_ever(db, user.id, action):
        return None
    pts = points if points is not None else default_points
    db.add(PointsLog(
        user_id=user.id,
        action=action,
        points=pts,
        description=description or desc_default,
        ref_type=ref_type,
        ref_id=ref_id,
    ))
    user.points_total += pts
    return pts


def spend(db: Session, user: User, points: int, description: str,
          ref_type: str | None = None, ref_id: int | None = None) -> bool:
    """扣积分（兑换奖励）。余额不足返回 False，同样不 commit 由调用方控制事务"""
    if user.points_total < points:
        return False
    user.points_total -= points
    db.add(PointsLog(
        user_id=user.id,
        action="redeem_reward",
        points=-points,
        description=description,
        ref_type=ref_type,
        ref_id=ref_id,
    ))
    return True
