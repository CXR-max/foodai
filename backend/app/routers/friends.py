"""好友：单向关注即可在领地地图「好友」筛选里看到对方的地。

不做申请/同意，避免为了一个筛选先做消息系统；互加就是互相可见。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.territory import TerritoryCell, TerritoryPref, UserFriend
from app.models.user import User
from app.services import territory as svc

router = APIRouter(prefix="/friends", tags=["好友"])


def friend_ids(db: Session, user_id: int) -> set[int]:
    rows = db.query(UserFriend.friend_id).filter(UserFriend.user_id == user_id).all()
    return {r[0] for r in rows}


@router.get("")
def list_friends(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ids = friend_ids(db, user.id)
    users = {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    counts = dict(db.query(TerritoryCell.owner_id, func.count(TerritoryCell.id))
                  .filter(TerritoryCell.owner_id.in_(ids))
                  .group_by(TerritoryCell.owner_id).all()) if ids else {}
    prefs = {p.user_id: p for p in db.query(TerritoryPref)
             .filter(TerritoryPref.user_id.in_(ids)).all()} if ids else {}
    items = []
    for fid in ids:
        u = users.get(fid)
        if not u:
            continue
        n = counts.get(fid, 0)
        items.append({
            "user_id": fid,
            "nickname": u.nickname or u.username,
            "color": (prefs[fid].color if prefs.get(fid) and prefs[fid].color else svc.color_for(fid)),
            "cells": n,
            "area_m2": n * svc.CELL_M * svc.CELL_M,
        })
    items.sort(key=lambda x: x["cells"], reverse=True)
    return {"items": items}


@router.get("/suggest")
def suggest(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """附近有地、但还没加好友的人（按地盘大小排）。"""
    ids = friend_ids(db, user.id) | {user.id}
    rows = (db.query(TerritoryCell.owner_id, func.count(TerritoryCell.id).label("n"))
            .filter(TerritoryCell.owner_id.isnot(None))
            .filter(TerritoryCell.owner_id.notin_(ids))
            .group_by(TerritoryCell.owner_id)
            .order_by(func.count(TerritoryCell.id).desc()).limit(8).all())
    uids = [r[0] for r in rows]
    users = {u.id: u for u in db.query(User).filter(User.id.in_(uids)).all()} if uids else {}
    prefs = {p.user_id: p for p in db.query(TerritoryPref)
             .filter(TerritoryPref.user_id.in_(uids)).all()} if uids else {}
    return {"items": [{
        "user_id": uid,
        "nickname": (users[uid].nickname or users[uid].username) if uid in users else "",
        "color": (prefs[uid].color if prefs.get(uid) and prefs[uid].color else svc.color_for(uid)),
        "cells": n,
        "area_m2": n * svc.CELL_M * svc.CELL_M,
    } for uid, n in rows]}


@router.post("/{friend_id}")
def add_friend(friend_id: int, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    if friend_id == user.id:
        raise HTTPException(status_code=400, detail="不能加自己")
    if not db.get(User, friend_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    existed = (db.query(UserFriend)
               .filter(UserFriend.user_id == user.id, UserFriend.friend_id == friend_id)
               .first())
    if not existed:
        db.add(UserFriend(user_id=user.id, friend_id=friend_id, created_at=datetime.now()))
        db.commit()
    return {"ok": True, "friend_id": friend_id}


@router.delete("/{friend_id}")
def remove_friend(friend_id: int, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    db.query(UserFriend).filter(UserFriend.user_id == user.id,
                                UserFriend.friend_id == friend_id).delete()
    db.commit()
    return {"ok": True}
