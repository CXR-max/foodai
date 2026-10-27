"""积分路由：流水 / 实时排行榜 / 我的排名"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.points_log import PointsLog
from app.models.user import User

router = APIRouter(prefix="/points", tags=["积分排行"])


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


@router.get("/leaderboard")
def leaderboard(limit: int = Query(default=20, ge=1, le=100),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """实时排行榜：按积分总额降序，同分同名次"""
    users = (db.query(User).order_by(User.points_total.desc(), User.id.asc()).limit(limit).all())
    result, last_points, last_rank = [], None, 0
    for i, u in enumerate(users, start=1):
        rank = last_rank if u.points_total == last_points else i
        result.append({
            "rank": rank,
            "user_id": u.id,
            "nickname": u.nickname or u.username,
            "points_total": u.points_total,
            "is_me": u.id == user.id,
        })
        last_points, last_rank = u.points_total, rank
    return result


@router.get("/me")
def my_rank(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """我的积分与排名"""
    higher = db.query(User).filter(User.points_total > user.points_total).count()
    return {"points_total": user.points_total, "rank": higher + 1,
            "nickname": user.nickname or user.username}
