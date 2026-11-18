"""积分商城路由：奖励列表 / 兑换（扣分事务）/ 我的兑换 / 使用券

兑换流程：
- redeem：积分足够才扣分（原子条件扣减防超扣），生成 8 位核销码，状态初始 unused
- use：使用权益券。补签卡(kind=makeup) → 为指定日期补一条打卡并给补签积分；
        其它券 → 标记已使用（线下核销/已兑换权益）
"""
import secrets
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.check_in import CheckIn
from app.models.redemption import Redemption
from app.models.reward import Reward
from app.models.user import User
from app.schemas.reward import UseRedemptionIn
from app.services import points
from app.services.achievements import unlock_all

router = APIRouter(prefix="/rewards", tags=["积分商城"])


def reward_dict(r: Reward, user_points: int | None = None) -> dict:
    result = {
        "id": r.id, "name": r.name, "category": r.category, "kind": r.kind,
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
    """兑换：积分足够才扣分（事务回滚保护），生成 8 位核销码，状态初始 unused"""
    reward = db.get(Reward, reward_id)
    if not reward or not reward.is_active:
        raise HTTPException(status_code=404, detail="奖励不存在")
    if reward.stock == 0:
        raise HTTPException(status_code=400, detail="该奖励已兑换完")
    if (user.points_total or 0) < reward.points_cost:
        raise HTTPException(status_code=400, detail=f"积分不足，还差 {reward.points_cost - (user.points_total or 0)} 分")

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
        points_spent=reward.points_cost, code=code, status="unused",
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
    """我的兑换记录（含核销码与使用状态）"""
    rows = (db.query(Redemption, Reward)
            .join(Reward, Redemption.reward_id == Reward.id)
            .filter(Redemption.user_id == user.id)
            .order_by(Redemption.created_at.desc()).all())
    return [{
        "id": rd.id, "name": reward.name, "icon": reward.icon,
        "category": reward.category, "kind": reward.kind,
        "points_spent": rd.points_spent, "code": rd.code, "status": rd.status,
        "used_at": rd.used_at.isoformat() if rd.used_at else None,
        "created_at": rd.created_at.isoformat() if rd.created_at else None,
    } for rd, reward in rows]


@router.post("/redemptions/{redemption_id}/use")
def use_redemption(
    redemption_id: int,
    data: UseRedemptionIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """使用权益券：补签卡 → 补签打卡并给积分；其它券 → 标记已使用"""
    rd = db.get(Redemption, redemption_id)
    if not rd or rd.user_id != user.id:
        raise HTTPException(status_code=404, detail="兑换记录不存在")
    if rd.status == "used":
        raise HTTPException(status_code=400, detail="该券已使用过")
    reward = db.get(Reward, rd.reward_id)
    if not reward:
        raise HTTPException(status_code=404, detail="奖励不存在")

    points_awarded = 0
    if reward.kind == "makeup":
        # 补签卡：为目标日期补一条"已打卡"记录，并给补签积分
        day = date.fromisoformat(data.check_date) if data.check_date else date.today()
        if day > date.today():
            raise HTTPException(status_code=400, detail="不能补签未来的日期")
        already = (db.query(CheckIn)
                   .filter(CheckIn.user_id == user.id, CheckIn.check_date == day,
                           CheckIn.status == "done").first())
        if already:
            raise HTTPException(status_code=400, detail="该日期已有打卡记录，无需补签")
        c = CheckIn(user_id=user.id, check_date=day, check_type="diet",
                    task_key="makeup", status="done", note="使用补签卡补签")
        db.add(c)
        db.flush()
        pts = points.award(db, user, "makeup_checkin",
                           description=f"补签 {day}（补签卡）",
                           ref_type="makeup", ref_id=c.id) or 0
        c.points_awarded = pts
        points_awarded = pts
        message = f"补签成功（{day}） +{pts} 分"
    else:
        message = f"「{reward.name}」已使用"

    rd.status = "used"
    rd.used_at = datetime.now()
    unlock_all(db, user)   # 补签可能改变连续打卡，触发成就解锁
    db.commit()
    return {"ok": True, "message": message, "points_awarded": points_awarded,
            "points_total": user.points_total}
