"""积分路由：流水 / 实时排行榜 / 我的排名

排行榜：优先走 Redis ZSet（工业方案，O(logN) 排序）；Redis 不可用时自动降级
SQLite 查询，功能不中断。两条链路都会带上"装备中的头衔/徽章"供排行榜展示。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.achievement import Achievement
from app.models.points_log import PointsLog
from app.models.user import User
from app.services.redis_leaderboard import leaderboard as redis_lb

router = APIRouter(prefix="/points", tags=["积分排行"])


def _achievement_map(db: Session) -> dict[int, Achievement]:
    return {a.id: a for a in db.query(Achievement).all()}


def _item(u: User, ach: dict[int, Achievement], rank: int) -> dict:
    eq = ach.get(u.equipped_achievement_id)
    return {
        "rank": rank,
        "user_id": u.id,
        "nickname": u.nickname or u.username,
        "points_total": u.points_total or 0,
        "equipped_title": eq.name if eq else None,
        "equipped_icon": eq.icon if eq else None,
    }


@router.get("/logs")
def my_logs(page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=50),
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """我的积分流水（分页，倒序）"""
    q = db.query(PointsLog).filter(PointsLog.user_id == user.id)
    total = q.count()
    items = (q.order_by(PointsLog.created_at.desc())
             .offset((page - 1) * page_size).limit(page_size).all())
    return {
        "total": total,
        "items": [{
            "id": log.id, "action": log.action, "points": log.points,
            "description": log.description,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        } for log in items],
    }


def _build_result(users: list[User], ach: dict[int, Achievement], me_id: int) -> list[dict]:
    """按传入顺序（已按积分降序）组装排行榜，处理同分同名次"""
    result, last_points, last_rank = [], None, 0
    for i, u in enumerate(users, start=1):
        rank = last_rank if (u.points_total or 0) == last_points else i
        item = _item(u, ach, rank)
        item["is_me"] = u.id == me_id
        result.append(item)
        last_points, last_rank = (u.points_total or 0), rank
    return result


@router.get("/leaderboard")
def leaderboard(limit: int = Query(default=20, ge=1, le=100),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """实时排行榜：按积分总额降序，同分同名次，带装备头衔

    优先读 Redis ZSet（O(logN)）；当 ZSet 数据为空/脏数据（如刚启动、新用户未入榜）
    时，自动回退 SQLite（源真值）并顺带重建 ZSet，保证下次走 Redis 快速路径。
    """
    ach = _achievement_map(db)

    # 1) 优先 Redis ZSet
    top = redis_lb.top(limit)
    if top:
        uids = [uid for uid, _ in top]
        users = {u.id: u for u in db.query(User).filter(User.id.in_(uids)).all()}
        if users:
            ordered = [users[uid] for uid, _ in top if uid in users]
            if ordered:
                return _build_result(ordered, ach, user.id)

    # 2) 回退 SQLite（源真值），并顺带重建 ZSet 保证后续一致
    all_users = (db.query(User)
                 .order_by(User.points_total.desc(), User.id.asc()).all())
    redis_lb.rebuild({u.id: (u.points_total or 0) for u in all_users})
    return _build_result(all_users[:limit], ach, user.id)


@router.get("/me")
def my_rank(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """我的积分与排名（优先 Redis，降级 SQLite）"""
    ach = _achievement_map(db)
    rank = redis_lb.rank(user.id)
    if rank is None:
        higher = db.query(User).filter(User.points_total > (user.points_total or 0)).count()
        rank = higher + 1
    eq = ach.get(user.equipped_achievement_id)
    return {"points_total": user.points_total or 0, "rank": rank,
            "nickname": user.nickname or user.username,
            "equipped_title": eq.name if eq else None,
            "equipped_icon": eq.icon if eq else None}
