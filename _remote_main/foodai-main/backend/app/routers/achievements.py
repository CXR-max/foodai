"""成就徽章路由：查看总览 / 装备头衔 / 卸下

成就解锁采用「重算 + 幂等解锁」：打开成就页时按用户当前各项指标（连续打卡、
累计识别/保存、积分、方案数等）重新计算，把达标且尚未解锁的成就批量解锁。
因此任何业务动作（打卡/识别/生成方案）完成后，用户下次进入成就页即可看到新解锁。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.achievement import Achievement, UserAchievement
from app.models.user import User
from app.schemas.achievement import EquipAchievementIn
from app.services.achievements import achievement_dict, unlock_all

router = APIRouter(prefix="/achievements", tags=["成就徽章"])


@router.get("")
def list_achievements(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """我的成就总览：全部成就 + 是否解锁 + 是否装备（进入即自动解锁达标项）"""
    # 先解锁达标但尚未解锁的成就（幂等），让前端打开页面时就能看到最新进度
    unlocked_new = unlock_all(db, user)
    db.commit()

    owned = {ua.achievement_id: ua.unlocked_at for ua in
             db.query(UserAchievement).filter(UserAchievement.user_id == user.id).all()}
    equipped = user.equipped_achievement_id
    items = []
    for a in db.query(Achievement).order_by(Achievement.id.asc()).all():
        ua_at = owned.get(a.id)
        items.append(achievement_dict(a, a.id in owned, a.id == equipped, ua_at))
    return {
        "items": items,
        "equipped_achievement_id": equipped,
        "unlocked_count": len(owned),
        "newly_unlocked": [a.name for a in unlocked_new],
    }


@router.post("/equip")
def equip(data: EquipAchievementIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """装备/卸下头衔或徽章（achievement_id 传 null 表示卸下）"""
    if data.achievement_id is None:
        user.equipped_achievement_id = None
        db.commit()
        return {"ok": True, "equipped_achievement_id": None, "message": "已卸下头衔"}

    a = db.get(Achievement, data.achievement_id)
    if not a:
        raise HTTPException(status_code=404, detail="成就不存在")
    owned = (db.query(UserAchievement)
             .filter(UserAchievement.user_id == user.id,
                     UserAchievement.achievement_id == a.id).first())
    if not owned:
        raise HTTPException(status_code=400, detail="尚未解锁该成就，无法装备")
    user.equipped_achievement_id = a.id
    db.commit()
    return {"ok": True, "equipped_achievement_id": a.id, "message": f"已装备「{a.name}」"}
