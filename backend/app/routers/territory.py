"""领地跑路由：地图、我的领地、开关、排行榜、动态"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.territory import (TerritoryCell, TerritoryEvent, TerritoryPref, TerritoryRun)
from app.models.user import User
from app.models.workout import Workout
from app.services import territory as svc

router = APIRouter(prefix="/territory", tags=["领地跑"])


class PrefsIn(BaseModel):
    enabled: bool | None = None
    anonymous: bool | None = None
    home_lat: float | None = None
    home_lng: float | None = None
    home_radius_m: int | None = None


@router.get("/map")
def map_view(
    min_lat: float = Query(...), max_lat: float = Query(...),
    min_lng: float = Query(...), max_lng: float = Query(...),
    scope: str = Query(default="all", pattern="^(all|friends|me)$"),
    owner_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回 bbox 内已占领的格子。只返回归属/活力，不返回轨迹与时间。

    scope=all 所有人；friends 我和我加的好友；me 只看自己。
    owner_id 用于「只看某个朋友」。
    """
    if max_lat <= min_lat or max_lng <= min_lng:
        raise HTTPException(status_code=400, detail="地图范围不正确")
    if (max_lat - min_lat) > 0.25 or (max_lng - min_lng) > 0.25:
        raise HTTPException(status_code=400, detail="视野太大，请放大地图")
    friends = svc.friend_ids(db, user.id)
    if owner_id:
        allowed = {owner_id}
    elif scope == "me":
        allowed = {user.id}
    elif scope == "friends":
        allowed = friends | {user.id}
    else:
        allowed = None
    return svc.map_cells(db, min_lat, max_lat, min_lng, max_lng, user, allowed)


@router.get("/me")
def my_territory(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    data = svc.me(db, user)
    data["recent_events"] = svc.recent_events(db, user, limit=8)
    return data


@router.post("/prefs")
def update_prefs(data: PrefsIn, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    pref = svc.get_pref(db, user.id)
    if data.enabled is not None:
        pref.enabled = data.enabled
        if not data.enabled:
            # 隐私优先：关闭立即释放，不保留 7 天
            cells = db.query(TerritoryCell).filter(TerritoryCell.owner_id == user.id).all()
            now = datetime.now()
            for c in cells:
                c.prev_owner_id = user.id
                c.owner_id = None
                c.vitality = 0.0
                c.state = "neutral"
                c.owner_changed_at = now
            db.add(TerritoryEvent(type="leave", cell_key="*", actor_id=user.id, created_at=now,
                                  meta={"cells": len(cells)}))
    if data.anonymous is not None:
        pref.anonymous = data.anonymous
    if data.home_lat is not None and data.home_lng is not None:
        pref.home_lat = round(data.home_lat, 3)
        pref.home_lng = round(data.home_lng, 3)
    if data.home_radius_m is not None:
        pref.home_radius_m = max(100, min(1000, data.home_radius_m))
    db.commit()
    return {"ok": True, "enabled": bool(pref.enabled), "anonymous": bool(pref.anonymous)}


@router.get("/leaderboard")
def leaderboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (db.query(TerritoryCell.owner_id, func.count(TerritoryCell.id).label("n"))
            .filter(TerritoryCell.owner_id.isnot(None))
            .group_by(TerritoryCell.owner_id)
            .order_by(func.count(TerritoryCell.id).desc()).limit(10).all())
    ids = [r[0] for r in rows]
    users = {u.id: (u.nickname or u.username) for u in
             db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    anon = {p.user_id for p in db.query(TerritoryPref)
            .filter(TerritoryPref.user_id.in_(ids), TerritoryPref.anonymous.is_(True)).all()} if ids else set()
    return {"items": [
        {"user_id": uid, "nickname": "匿名跑者" if uid in anon else users.get(uid, ""),
         "cells": n, "area_m2": n * svc.CELL_M * svc.CELL_M, "color": svc.color_for(uid)}
        for uid, n in rows
    ]}


@router.get("/owners/{owner_id}")
def owner_detail(owner_id: int, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """某个人的领地详情 + 最近占领记录 + 被抢/抢回动态。"""
    owner = db.get(User, owner_id)
    if not owner:
        raise HTTPException(status_code=404, detail="用户不存在")
    pref = db.query(TerritoryPref).filter(TerritoryPref.user_id == owner_id).first()
    cells = db.query(TerritoryCell).filter(TerritoryCell.owner_id == owner_id).all()
    svc.decay_cells(db, cells)
    db.commit()
    cells = [c for c in cells if c.owner_id]
    area = len(cells) * svc.CELL_M * svc.CELL_M
    # 最大连片区
    keys = {svc.key_ixiy(c.cell_key) for c in cells}
    seen: set[tuple[int, int]] = set()
    largest = 0
    for start in keys:
        if start in seen:
            continue
        q = [start]
        seen.add(start)
        size = 0
        while q:
            x, y = q.pop()
            size += 1
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in keys and n not in seen:
                    seen.add(n)
                    q.append(n)
        largest = max(largest, size)
    counts = dict(db.query(TerritoryCell.owner_id, func.count(TerritoryCell.id))
                  .filter(TerritoryCell.owner_id.isnot(None))
                  .group_by(TerritoryCell.owner_id).all())
    mine = counts.get(owner_id, 0)
    rank = 1 + sum(1 for v in counts.values() if v > mine)

    overview = svc.owner_overview(cells)
    record = svc.head_to_head(db, user.id, owner_id)
    challenge = None if owner_id == user.id else svc.active_challenge(db, user.id, owner_id)

    rows = (db.query(TerritoryRun, Workout)
            .join(Workout, Workout.id == TerritoryRun.workout_id)
            .filter(TerritoryRun.user_id == owner_id, TerritoryRun.valid.is_(True))
            .order_by(TerritoryRun.created_at.desc()).limit(8).all())
    runs = [{
        "id": r.id,
        "start_at": w.start_at.isoformat() if w.start_at else None,
        "sport_type": w.sport_type,
        "distance_m": w.distance_m or 0,
        "duration_sec": w.duration_sec or 0,
        "avg_pace_sec_km": w.avg_pace_sec_km,
        "calories": w.calories or 0,
        "area_new_m2": (r.cells_new or 0) * svc.CELL_M * svc.CELL_M,
        "area_cut_m2": (r.cells_cut or 0) * svc.CELL_M * svc.CELL_M,
        "loop": bool(r.loop),
    } for r, w in rows]

    ev_rows = (db.query(TerritoryEvent)
               .filter(or_(TerritoryEvent.actor_id == owner_id,
                           TerritoryEvent.victim_id == owner_id))
               .order_by(TerritoryEvent.created_at.desc()).limit(20).all())
    uids = {e.actor_id for e in ev_rows if e.actor_id} | {e.victim_id for e in ev_rows if e.victim_id}
    names = {u.id: (u.nickname or u.username) for u in
             db.query(User).filter(User.id.in_(uids)).all()} if uids else {}
    events = [{
        "id": e.id,
        "type": e.type,
        "actor": names.get(e.actor_id, ""),
        "actor_id": e.actor_id,
        "victim": names.get(e.victim_id, ""),
        "victim_id": e.victim_id,
        "area_m2": (e.meta or {}).get("area_m2", 0),
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in ev_rows]

    friends = svc.friend_ids(db, user.id)
    return {
        "user_id": owner_id,
        "nickname": owner.nickname or owner.username,
        "color": (pref.color if pref and pref.color else svc.color_for(owner_id)),
        "area_m2": area,
        "largest_block_area_m2": largest * svc.CELL_M * svc.CELL_M,
        "rank": rank,
        "is_me": owner_id == user.id,
        "is_friend": owner_id in friends,
        "can_attack": owner_id != user.id and owner_id in friends,
        "tier": svc.tier_of(area),
        "record": record,
        "challenge": challenge,
        "runs": runs,
        "events": events,
        **overview,
    }


class ChallengeIn(BaseModel):
    target_id: int


@router.post("/challenges")
def send_challenge(data: ChallengeIn, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """下战书：24h 内真的切走对方一块地算赢。"""
    try:
        return svc.create_challenge(db, user, data.target_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/challenges/{challenge_id}/accept")
def accept_challenge(challenge_id: int, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """应战：只是留个记号（再跑一圈巩固即可防守），不改格子的归属。"""
    try:
        return svc.accept_challenge(db, user, challenge_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/runs/{workout_id}")
def run_detail(workout_id: int, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """一次跑步的领地结算。地图页的圈地动画和「跑完整屏结算」读的都是它。"""
    data = svc.settlement(db, user, workout_id)
    if not data:
        raise HTTPException(status_code=404, detail="这次运动还没有领地结算")
    return data
