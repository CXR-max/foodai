"""积分商城路由：奖励列表 / 兑换（扣分事务）/ 我的兑换"""
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.redemption import Redemption
from app.models.reward import Reward
from app.models.user import User
from app.services import points

router = APIRouter(prefix="/rewards", tags=["积分商城"])


def reward_dict(r: Reward, user_points: int | None = None) -> dict:
    result = {
        "id": r.id, "name": r.name, "category": r.category,
        "description": r.description, "points_cost": r.points_cost,
        "stock": r.stock, "icon": r.icon, "is_active": r.is_active,
    }
    if user_points is not None:
        result["can_afford"] = user_points >= r.points_cost and (r.stock == -1 or r.stock > 0)
    return result


@router.get("")
def list_rewards(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """奖励商品列表（带"我的积分够不够换"标记）"""
    rewards = db.query(Reward).filter(Reward.is_active == True).order_by(Reward.points_cost.asc()).all()  # noqa: E712
    return [reward_dict(r, user.points_total) for r in rewards]


@router.post("/{reward_id}/redeem")
def redeem(reward_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """兑换：积分足够才扣分（事务回滚保护），生成 8 位核销码"""
    reward = db.get(Reward, reward_id)
    if not reward or not reward.is_active:
        raise HTTPException(status_code=404, detail="奖励不存在")
    if reward.stock == 0:
        raise HTTPException(status_code=400, detail="该奖励已兑换完")
    if user.points_total < reward.points_cost:
        raise HTTPException(status_code=400, detail=f"积分不足，还差 {reward.points_cost - user.points_total} 分")

    # 扣分（spend 内部再校验一次余额，保证并发安全）
    ok = points.spend(db, user, reward.points_cost,
                      description=f"兑换奖励：{reward.name}", ref_type="reward", ref_id=reward.id)
    if not ok:
        raise HTTPException(status_code=400, detail="积分不足")
    if reward.stock > 0:
        reward.stock -= 1

    code = secrets.token_hex(4).upper()   # 8 位核销码
    redemption = Redemption(
        user_id=user.id, reward_id=reward.id,
        points_spent=reward.points_cost, code=code,
    )
    db.add(redemption)
    db.commit()
    db.refresh(redemption)
    return {
        "id": redemption.id, "code": code,
        "name": reward.name, "points_spent": reward.points_cost,
        "points_total": user.points_total,
        "message": f"兑换成功！核销码：{code}",
    }


@router.get("/redemptions")
def my_redemptions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """我的兑换记录（含核销码）"""
    rows = (db.query(Redemption, Reward)
            .join(Reward, Redemption.reward_id == Reward.id)
            .filter(Redemption.user_id == user.id)
            .order_by(Redemption.created_at.desc()).all())
    return [{
        "id": rd.id, "name": reward.name, "icon": reward.icon,
        "points_spent": rd.points_spent, "code": rd.code, "status": rd.status,
        "created_at": rd.created_at.isoformat() if rd.created_at else None,
    } for rd, reward in rows]
