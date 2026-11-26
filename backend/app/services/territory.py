"""领地跑核心服务：网格 / 落格 / 闭环填充 / 结算 / 衰减 / 地图查询

刻意与前端解耦：所有距离、归属、活力都在后端算，前端只负责把格子画成轮廓。
"""
from __future__ import annotations

import logging
import math
from collections import Counter, deque
from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.models.territory import (TerritoryCell, TerritoryChallenge, TerritoryEvent,
                                  TerritoryPref, TerritoryRun, UserFriend)
from app.models.user import User
from app.models.workout import Workout

logger = logging.getLogger("territory")

# ---------------- 网格 ----------------
CELL_M = 25.0
DLAT = CELL_M / 111_320.0
DLNG = CELL_M / (111_320.0 * math.cos(math.radians(31.23)))
EARTH_M = 6_371_000.0

# 规则参数（改这里就能调玩法）
MAX_RUN_CELLS = 128          # 单次跑步最多占地（含填充）
MAX_FILL_CELLS = 128         # 单次闭环内场最多 128 格 ≈ 0.08km²
LOOP_TOL_M = 50.0            # 首尾距离（KYRO 严格版）
MIN_RUN_SEC = 90             # 400m 操场一圈（含快跑）也要能圈；只防原地
MIN_RUN_DIST_M = 150.0       # 不要求 600m；靠闭环和面积上限防刷
MIN_POINTS = 20
MAX_ACC_M = 25.0
DECAY_PER_DAY = 8.0
ATTACK_BASE = 45.0
ATTACK_DEFENDED = 27.0
ATTACK_RECAPTURE = 90.0
NEW_OWNER_VITALITY = 55.0
REPAIR_VITALITY = 20.0
FILL_VITALITY = 50.0
# 曾经是 15 分钟：为了不让别人通过「地图形状变化」推测你的实时位置。
# 但地图只返回色块、不返回轨迹，15 分钟唯一的效果是「被抢了当场看不见」，
# 竞争感全靠这一刻，所以取消延迟。要恢复改回 15 即可。
OWNER_DELAY_MIN = 0
RECAPTURE_HOURS = 48         # 被抢后 48h 内跑回去，一次就能拿回来（ATTACK_RECAPTURE > 新主人的 55 活力）
CHALLENGE_HOURS = 24         # 战书有效期：24h 内切走对方一块地即算赢

# 段位：按总面积（㎡）划分，名字要能从「一个人跑操场」想到「整片校区」
TIERS: list[tuple[int, str, int]] = [
    (1, "刚起步", 0),
    (2, "街角跑者", 5_000),
    (3, "操场常客", 20_000),
    (4, "街区领主", 60_000),
    (5, "校园霸主", 150_000),
]


def tier_of(area_m2: float) -> dict:
    """段位 + 到下一段位还差多少。纯函数，前端也能从 area 反推同一套规则。"""
    area = max(0.0, float(area_m2 or 0))
    cur = TIERS[0]
    for t in TIERS:
        if area >= t[2]:
            cur = t
    nxt = next((t for t in TIERS if t[0] == cur[0] + 1), None)
    if not nxt:
        return {"level": cur[0], "name": cur[1], "area_m2": area,
                "next_name": "", "next_area_m2": 0, "progress": 1.0, "to_next_m2": 0}
    span = max(1, nxt[2] - cur[2])
    return {
        "level": cur[0], "name": cur[1], "area_m2": area,
        "next_name": nxt[1], "next_area_m2": nxt[2],
        "progress": round(min(1.0, max(0.0, (area - cur[2]) / span)), 3),
        "to_next_m2": max(0, nxt[2] - int(area)),
    }

PALETTE = ["#3b82f6", "#f97316", "#10b981", "#8b5cf6", "#ef4444",
           "#06b6d4", "#eab308", "#ec4899", "#14b8a6", "#6366f1"]


def color_for(user_id: int | None) -> str:
    return PALETTE[(user_id or 0) % len(PALETTE)]


def cell_key(lat: float, lng: float) -> str:
    ix = math.floor(lng / DLNG)
    iy = math.floor(lat / DLAT)
    return f"t25_{ix}_{iy}"


def key_ixiy(key: str) -> tuple[int, int]:
    _, ix, iy = key.split("_")
    return int(ix), int(iy)


def key_center(key: str) -> tuple[float, float]:
    ix, iy = key_ixiy(key)
    return (iy + 0.5) * DLAT, (ix + 0.5) * DLNG


def key_bounds(key: str) -> tuple[float, float, float, float]:
    """返回 (min_lat, min_lng, max_lat, max_lng)，WGS-84"""
    ix, iy = key_ixiy(key)
    return iy * DLAT, ix * DLNG, (iy + 1) * DLAT, (ix + 1) * DLNG


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1 = a
    lat2, lng2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(h))


def track_length_m(track: list[dict]) -> float:
    total = 0.0
    for a, b in zip(track, track[1:]):
        total += haversine_m((a["lat"], a["lng"]), (b["lat"], b["lng"]))
    return total


def rasterize(track: list[dict], step_m: float = 3.0, min_samples: int = 2) -> list[str]:
    """轨迹落格：每 3m 采样，同一格至少 2 个采样点才算；按轨迹首次出现顺序返回。"""
    cnt: Counter[str] = Counter()
    order: list[str] = []
    for a, b in zip(track, track[1:]):
        lat1, lng1 = float(a["lat"]), float(a["lng"])
        lat2, lng2 = float(b["lat"]), float(b["lng"])
        d = haversine_m((lat1, lng1), (lat2, lng2))
        n = max(1, int(d / step_m))
        for i in range(n + 1):
            t = i / n
            k = cell_key(lat1 + (lat2 - lat1) * t, lng1 + (lng2 - lng1) * t)
            if k not in cnt:
                order.append(k)
            cnt[k] += 1
    return [k for k in order if cnt[k] >= min_samples]


def _bridge_gaps(cells: set[tuple[int, int]]) -> set[tuple[int, int]]:
    blocked = set(cells)
    for x, y in cells:
        if (x + 2, y) in cells and (x + 1, y) not in cells:
            blocked.add((x + 1, y))
        if (x, y + 2) in cells and (x, y + 1) not in cells:
            blocked.add((x, y + 1))
    return blocked


def _flood_interior(ixys: set[tuple[int, int]],
                    blocked: set[tuple[int, int]]) -> list[tuple[int, int]]:
    xs = [c[0] for c in ixys]
    ys = [c[1] for c in ixys]
    x0, x1 = min(xs) - 3, max(xs) + 3
    y0, y1 = min(ys) - 3, max(ys) + 3

    seen: set[tuple[int, int]] = set()
    q: deque[tuple[int, int]] = deque()
    for x in range(x0, x1 + 1):
        for y in (y0, y1):
            if (x, y) not in blocked and (x, y) not in seen:
                seen.add((x, y)); q.append((x, y))
    for y in range(y0, y1 + 1):
        for x in (x0, x1):
            if (x, y) not in blocked and (x, y) not in seen:
                seen.add((x, y)); q.append((x, y))

    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, y + dy)
            if not (x0 <= n[0] <= x1 and y0 <= n[1] <= y1):
                continue
            if n in blocked or n in seen:
                continue
            seen.add(n); q.append(n)

    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)
            if (x, y) not in blocked and (x, y) not in seen]


def fill_interior_detail(trace_keys: list[str]) -> tuple[list[str], str]:
    """闭环内场：轨迹格当墙，从 bbox 边界泛洪。

    - 不再用「每格离轨迹 ≤60m」：小/中圈整片填充，才像 KYRO 的圈地；
    - 用 128 格（约 0.08km²）作面积上限，防止一圈吞校区；
    - 不要求首尾 50m：只要轨迹自身围出了封闭区域（lollipop / 自交）就算。
    """
    if not trace_keys:
        return [], "没有轨迹"
    ixys = {key_ixiy(k) for k in trace_keys}
    interior = _flood_interior(ixys, set(ixys))
    if not interior:
        interior = _flood_interior(ixys, _bridge_gaps(ixys))
    if not interior:
        return [], "未形成闭环，只记轨迹"
    if len(interior) > MAX_FILL_CELLS:
        return [], f"圈太大（{len(interior) * CELL_M * CELL_M / 10000:.1f} 万㎡），最多 0.08km²，请跑小一点"
    return [f"t25_{x}_{y}" for x, y in interior], "ok"


def fill_interior(trace_keys: list[str]) -> list[str]:
    """兼容种子/旧调用：只返回格子，不关心原因。"""
    cells, _ = fill_interior_detail(trace_keys)
    return cells


def is_closed(track: list[dict]) -> bool:
    return (len(track) >= 3
            and haversine_m((track[0]["lat"], track[0]["lng"]),
                            (track[-1]["lat"], track[-1]["lng"])) <= LOOP_TOL_M)


# ---------------- 用户偏好 ----------------
def get_pref(db: Session, user_id: int) -> TerritoryPref:
    p = db.get(TerritoryPref, user_id)
    if p is None:
        p = TerritoryPref(user_id=user_id, enabled=False, anonymous=False)
        db.add(p)
        db.flush()
    return p


# ---------------- eligibility ----------------
def _median(vals: list[float]) -> float:
    vals = sorted(vals)
    n = len(vals)
    if not n:
        return 0.0
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def eligibility(workout: Workout) -> tuple[bool, str]:
    if workout.source != "gps":
        return False, "不是 GPS 轨迹"
    if workout.sport_type not in ("跑步", "快走"):
        return False, "只认跑步/快走"
    if (workout.duration_sec or 0) < MIN_RUN_SEC:
        return False, "不足 6 分钟"
    if (workout.distance_m or 0) < MIN_RUN_DIST_M:
        return False, "不足 600 米"
    track = workout.track or []
    if len(track) < MIN_POINTS:
        return False, "有效点不足"
    accs = [float(p.get("acc")) for p in track if p.get("acc") is not None]
    if accs and _median(accs) > MAX_ACC_M:
        return False, "定位精度不足"
    speed_kmh = (workout.distance_m or 0) / (workout.duration_sec or 1) * 3.6
    if speed_kmh < 2.0 or speed_kmh > 18.0:
        return False, "速度不合理"
    return True, "ok"


# ---------------- 衰减 ----------------
def _release(cell: TerritoryCell, now: datetime) -> None:
    cell.prev_owner_id = cell.owner_id
    cell.owner_id = None
    cell.vitality = 0.0
    cell.state = "neutral"
    cell.owner_changed_at = now


def decay_cells(db: Session, cells: list[TerritoryCell]) -> bool:
    today = date.today()
    now = datetime.now()
    changed = False
    for c in cells:
        if not c.owner_id:
            continue
        if c.last_decay_date is None:
            c.last_decay_date = today
            continue
        days = (today - c.last_decay_date).days
        if days <= 0:
            continue
        c.vitality = max(0.0, (c.vitality or 0.0) - DECAY_PER_DAY * days)
        c.last_decay_date = today
        if c.vitality <= 0:
            _release(c, now)
            db.add(TerritoryEvent(type="decay", cell_key=c.cell_key, victim_id=c.prev_owner_id,
                                  created_at=now, meta={"days": days}))
        changed = True
    return changed


# ---------------- 结算一次跑步 ----------------
def claim_workout(db: Session, workout: Workout, user: User) -> dict | None:
    """把一次 GPS 跑步结算成领地。幂等：同一个 workout_id 只结算一次。"""
    existed = (db.query(TerritoryRun)
               .filter(TerritoryRun.workout_id == workout.id).first())
    if existed:
        return _run_dict(existed)

    pref = get_pref(db, user.id)
    if not pref.enabled:
        return None

    ok, reason = eligibility(workout)
    track = workout.track or []
    run = TerritoryRun(workout_id=workout.id, user_id=user.id, valid=ok, reason=reason,
                       created_at=datetime.now())
    if not ok:
        db.add(run)
        db.commit()
        return _run_dict(run)

    trace = rasterize(track)
    if not trace:
        run.reason = "没有落格"
        db.add(run)
        db.commit()
        return _run_dict(run)

    # 不再强制“首尾 50m”：轨迹自己围出封闭区域（lollipop / 自交）也算闭环。
    # fill_interior_detail 同时负责面积上限，超过 0.08km² 会拒绝。
    fill, fill_reason = fill_interior_detail(trace)
    if not fill:
        run.valid = False
        run.reason = fill_reason
        run.loop = False
        db.add(run)
        db.commit()
        return _run_dict(run)

    # 单次预算：先跑过的格，再填充；超出部分不占地
    trace = trace[:MAX_RUN_CELLS]
    fill = fill[:max(0, MAX_RUN_CELLS - len(trace))]

    now = datetime.now()
    new = attacked = stolen = repaired = cut = 0
    friends = friend_ids(db, user.id)
    touched: list[str] = []

    def apply(key: str, is_fill: bool) -> None:
        nonlocal new, attacked, stolen, repaired, cut
        ix, iy = key_ixiy(key)
        cell = db.query(TerritoryCell).filter(TerritoryCell.cell_key == key).first()
        if cell is None:
            cell = TerritoryCell(cell_key=key, ix=ix, iy=iy, vitality=0.0, state="neutral")
            db.add(cell)
            db.flush()
        touched.append(key)

        if not cell.owner_id:
            cell.owner_id = user.id
            cell.vitality = FILL_VITALITY if is_fill else 100.0
            cell.state = "owned"
            cell.first_claimed_at = now
            cell.last_visit_at = now
            cell.last_decay_date = date.today()
            cell.owner_changed_at = now
            new += 1
            return

        if cell.owner_id == user.id:
            cell.vitality = min(100.0, (cell.vitality or 0) + REPAIR_VITALITY)
            cell.last_visit_at = now
            cell.last_decay_date = date.today()
            repaired += 1
            return

        # 陌生人只能看，不能抢；只有好友之间能互相切地
        if cell.owner_id not in friends:
            return

        # 闭环内场 = 切地：围住的友军格直接归你，这是主玩法
        if is_fill:
            old_owner = cell.owner_id
            cell.prev_owner_id = old_owner
            cell.stolen_at = now
            cell.owner_id = user.id
            cell.vitality = NEW_OWNER_VITALITY
            cell.state = "owned"
            cell.owner_changed_at = now
            cell.last_visit_at = now
            cell.last_decay_date = date.today()
            cut += 1
            db.add(TerritoryEvent(type="cut", cell_key=key, actor_id=user.id,
                                  victim_id=old_owner, run_id=run.id, created_at=now,
                                  meta={"is_fill": True}))
            return

        # 轨迹压过边界 = 辅助磨活力，不直接翻转（除非活力已经很低）
        if cell.shield_until and cell.shield_until > now:
            cell.vitality = max(1.0, (cell.vitality or 0) - ATTACK_DEFENDED)
            attacked += 1
            return
        defended = bool(cell.last_visit_at and (now - cell.last_visit_at) < timedelta(hours=24))
        recapture = bool(cell.prev_owner_id == user.id and cell.stolen_at
                         and (now - cell.stolen_at) < timedelta(hours=48))
        dmg = ATTACK_RECAPTURE if recapture else (ATTACK_DEFENDED if defended else ATTACK_BASE)
        cell.vitality = (cell.vitality or 0) - dmg
        attacked += 1
        if cell.vitality <= 0:
            old_owner = cell.owner_id
            cell.prev_owner_id = old_owner
            cell.stolen_at = now
            cell.owner_id = user.id
            cell.vitality = NEW_OWNER_VITALITY
            cell.state = "owned"
            cell.owner_changed_at = now
            cell.last_visit_at = now
            cell.last_decay_date = date.today()
            stolen += 1
            db.add(TerritoryEvent(type="recapture" if recapture else "steal", cell_key=key,
                                  actor_id=user.id, victim_id=old_owner, run_id=run.id,
                                  created_at=now,
                                  meta={"recapture": recapture, "old_vitality": 0}))

    for k in trace:
        apply(k, False)
    for k in fill:
        apply(k, True)

    run.valid = True
    run.reason = "ok_loop"
    run.cells_new = new
    run.cells_filled = len(fill)
    run.cells_attacked = attacked
    run.cells_stolen = stolen
    run.cells_repaired = repaired
    run.cells_cut = cut
    run.loop = True
    run.cells = touched
    db.add(run)
    db.commit()
    return _run_dict(run)


def _run_dict(run: TerritoryRun) -> dict:
    return {
        "run_id": run.id,
        "workout_id": run.workout_id,
        "user_id": run.user_id,
        "valid": run.valid,
        "reason": run.reason,
        "cells_new": run.cells_new,
        "cells_filled": run.cells_filled,
        "cells_attacked": run.cells_attacked,
        "cells_stolen": run.cells_stolen,
        "cells_repaired": run.cells_repaired,
        "cells_cut": run.cells_cut or 0,
        "area_new_m2": (run.cells_new or 0) * CELL_M * CELL_M,
        "area_cut_m2": (run.cells_cut or 0) * CELL_M * CELL_M,
        "loop": run.loop,
        "cells": run.cells or [],
    }


# ---------------- 地图 / 统计 ----------------
def friend_ids(db: Session, user_id: int) -> set[int]:
    rows = db.query(UserFriend.friend_id).filter(UserFriend.user_id == user_id).all()
    return {r[0] for r in rows}


def _owner_map(db: Session, ids: set[int], viewer_id: int | None = None,
               friends: set[int] | None = None) -> dict[str, dict]:
    if not ids:
        return {}
    users = db.query(User).filter(User.id.in_(ids)).all()
    prefs = {p.user_id: p for p in db.query(TerritoryPref)
             .filter(TerritoryPref.user_id.in_(ids)).all()}
    counts = dict(db.query(TerritoryCell.owner_id, func.count(TerritoryCell.id))
                  .filter(TerritoryCell.owner_id.in_(ids))
                  .group_by(TerritoryCell.owner_id).all())
    out = {}
    for u in users:
        nick = "匿名跑者" if (prefs.get(u.id) and prefs[u.id].anonymous) else (u.nickname or u.username)
        out[str(u.id)] = {
            "nickname": nick,
            "color": (prefs.get(u.id).color if prefs.get(u.id) and prefs[u.id].color else color_for(u.id)),
            "cells": counts.get(u.id, 0),
            "is_me": u.id == viewer_id,
            "is_friend": bool(friends and u.id in friends),
        }
    return out


def map_cells(db: Session, min_lat: float, max_lat: float, min_lng: float, max_lng: float,
              viewer: User, allowed_ids: set[int] | None = None) -> dict:
    min_ix = math.floor(min_lng / DLNG)
    max_ix = math.floor(max_lng / DLNG)
    min_iy = math.floor(min_lat / DLAT)
    max_iy = math.floor(max_lat / DLAT)
    q = (db.query(TerritoryCell)
         .filter(TerritoryCell.ix.between(min_ix, max_ix),
                 TerritoryCell.iy.between(min_iy, max_iy),
                 TerritoryCell.owner_id.isnot(None)))
    if allowed_ids is not None:
        q = q.filter(TerritoryCell.owner_id.in_(allowed_ids))
    rows = q.limit(8000).all()
    changed = decay_cells(db, rows)
    if changed:
        db.commit()

    cutoff = datetime.now() - timedelta(minutes=OWNER_DELAY_MIN)
    now = datetime.now()
    cells = []
    owner_ids: set[int] = set()
    recapture: list[list] = []    # [cell_key, 剩余秒数]
    for c in rows:
        if not c.owner_id:
            continue
        # 别人的新变更延迟 N 分钟（默认 0 = 实时）；自己的实时
        if c.owner_id != viewer.id and c.owner_changed_at and c.owner_changed_at > cutoff:
            continue
        flags = 0
        if c.shield_until and c.shield_until > now:
            flags |= 1
        # 48h 内被别人抢走、原本是我的地：跑过去夺回伤害 ×2，地图上要标出来
        if (c.prev_owner_id == viewer.id and c.owner_id != viewer.id
                and c.stolen_at and (now - c.stolen_at) < timedelta(hours=RECAPTURE_HOURS)):
            flags |= 2
            left = int((timedelta(hours=RECAPTURE_HOURS) - (now - c.stolen_at)).total_seconds())
            recapture.append([c.cell_key, max(0, left)])
        cells.append([c.cell_key, c.owner_id, round(c.vitality or 0), flags])
        owner_ids.add(c.owner_id)

    # 无主地：曾经有人占、后来衰退或主动释放掉的地。空着但看得见，才会有「去认领」的冲动。
    # 只在「全部」视野里出现：筛选好友/自己时不该混进空地块。
    neutral_rows = []
    if allowed_ids is None:
        neutral_rows = (db.query(TerritoryCell)
                        .filter(TerritoryCell.ix.between(min_ix, max_ix),
                                TerritoryCell.iy.between(min_iy, max_iy),
                                TerritoryCell.owner_id.is_(None),
                                TerritoryCell.prev_owner_id.isnot(None))
                        .limit(2000).all())
    neutral = []
    for c in neutral_rows:
        hours = 0
        if c.owner_changed_at:
            hours = max(0, int((now - c.owner_changed_at).total_seconds() // 3600))
        neutral.append([c.cell_key, hours])

    return {"cell_m": CELL_M, "cells": cells, "neutral": neutral,
            "recapture": recapture,
            "owners": _owner_map(db, owner_ids, viewer.id, friend_ids(db, viewer.id)),
            "count": len(cells)}


def _largest_component(cells: list[TerritoryCell]) -> set[tuple[int, int]]:
    keys = {key_ixiy(c.cell_key) for c in cells}
    seen: set[tuple[int, int]] = set()
    best: set[tuple[int, int]] = set()
    for start in keys:
        if start in seen:
            continue
        q = deque([start])
        seen.add(start)
        comp: set[tuple[int, int]] = set()
        while q:
            x, y = q.popleft()
            comp.add((x, y))
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in keys and n not in seen:
                    seen.add(n); q.append(n)
        if len(comp) > len(best):
            best = comp
    return best


def _largest_block(cells: list[TerritoryCell]) -> int:
    return len(_largest_component(cells))


def stolen_from_me(db: Session, user_id: int, now: datetime | None = None) -> list[TerritoryCell]:
    """48h 内被人抢走、还没夺回的地块：跑过去伤害 ×2（ATTACK_RECAPTURE）。"""
    now = now or datetime.now()
    since = now - timedelta(hours=RECAPTURE_HOURS)
    return (db.query(TerritoryCell)
            .filter(TerritoryCell.prev_owner_id == user_id,
                    TerritoryCell.owner_id.isnot(None),
                    TerritoryCell.owner_id != user_id,
                    TerritoryCell.stolen_at.isnot(None),
                    TerritoryCell.stolen_at >= since)
            .all())


def rank_gap(db: Session, user_id: int) -> dict:
    """排名 + 「离上一名还差多少」：只有一个名次时没有意义，差距才有。"""
    counts = dict(db.query(TerritoryCell.owner_id, func.count(TerritoryCell.id))
                  .filter(TerritoryCell.owner_id.isnot(None))
                  .group_by(TerritoryCell.owner_id).all())
    mine = counts.get(user_id, 0)
    rank = 1 + sum(1 for v in counts.values() if v > mine)
    ahead = [(uid, v) for uid, v in counts.items() if uid != user_id and v > mine]
    if not ahead:
        return {"rank": rank, "ahead_name": "", "ahead_gap_m2": 0,
                "total_players": len(counts), "rank_of": len(counts)}
    uid, v = min(ahead, key=lambda x: x[1])
    return {
        "rank": rank,
        "ahead_name": _nick(db, uid),
        "ahead_gap_m2": (v - mine) * CELL_M * CELL_M,
        "total_players": len(counts),
        "rank_of": len(counts),
    }


def me(db: Session, user: User) -> dict:
    pref = get_pref(db, user.id)
    cells = db.query(TerritoryCell).filter(TerritoryCell.owner_id == user.id).all()
    if decay_cells(db, cells):
        db.commit()
    cells = [c for c in cells if c.owner_id]
    today = date.today()
    week_ago = datetime.now() - timedelta(days=7)
    today_new = sum(1 for c in cells if c.first_claimed_at and c.first_claimed_at.date() == today)
    week_new = sum(1 for c in cells if c.first_claimed_at and c.first_claimed_at >= week_ago)
    gap = rank_gap(db, user.id)
    rank = gap["rank"]
    component = _largest_component(cells)
    center = None
    if component:
        # 定位到最大那块地的中心，而不是所有地的平均（两块地离得远时平均会落在空地）
        pts = [key_center(f"t25_{x}_{y}") for x, y in component]
        center = [sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)]

    # 最近一次 GPS 跑步轨迹（只给自己看，用来在地图上画「我跑过的路线」）
    latest = (db.query(Workout)
              .filter(Workout.user_id == user.id, Workout.source == "gps",
                      Workout.track.isnot(None))
              .order_by(Workout.start_at.desc()).first())
    latest_track = []
    if latest and latest.track:
        pts = latest.track
        step = max(1, len(pts) // 300)
        latest_track = [[round(p["lat"], 6), round(p["lng"], 6)] for p in pts[::step]]
    vit = (sum(float(c.vitality or 0) for c in cells) / len(cells)) if cells else 0.0
    # 48h 内可 ×2 夺回的地块（别人抢走的、原本是你的）
    recapture_n = len(stolen_from_me(db, user.id))
    return {
        "user_id": user.id,
        "enabled": bool(pref.enabled),
        "anonymous": bool(pref.anonymous),
        "total_cells": len(cells),
        "area_m2": len(cells) * CELL_M * CELL_M,
        "largest_block": len(component),
        "largest_block_area_m2": len(component) * CELL_M * CELL_M,
        "rank": rank,
        "rank_of": gap["rank_of"],
        "ahead_name": gap["ahead_name"],
        "ahead_gap_m2": gap["ahead_gap_m2"],
        "vitality_avg": round(vit, 1),
        "days_left": max(0, math.ceil(vit / DECAY_PER_DAY)) if cells else 0,
        "tier": tier_of(len(cells) * CELL_M * CELL_M),
        "recapture_cells": recapture_n,
        "today_new": today_new,
        "week_new": week_new,
        "center": center,
        "latest_track": latest_track,
        "color": (pref.color or color_for(user.id)),
        "cell_m": CELL_M,
    }


def recent_events(db: Session, user: User, limit: int = 10) -> list[dict]:
    rows = (db.query(TerritoryEvent)
            .filter((TerritoryEvent.actor_id == user.id) | (TerritoryEvent.victim_id == user.id))
            .order_by(TerritoryEvent.id.desc()).limit(limit).all())
    names = {}
    ids = {e.actor_id for e in rows if e.actor_id} | {e.victim_id for e in rows if e.victim_id}
    for uid, nick, username in db.query(User.id, User.nickname, User.username).filter(User.id.in_(ids)).all():
        names[uid] = nick or username
    out = []
    for e in rows:
        out.append({"id": e.id, "type": e.type, "cell_key": e.cell_key,
                    "actor": names.get(e.actor_id, ""), "actor_id": e.actor_id,
                    "victim": names.get(e.victim_id, ""), "victim_id": e.victim_id,
                    "meta": e.meta or {},
                    "created_at": e.created_at.isoformat() if e.created_at else None})
    return out


def run_by_workout(db: Session, workout_id: int) -> dict | None:
    run = db.query(TerritoryRun).filter(TerritoryRun.workout_id == workout_id).first()
    return _run_dict(run) if run else None


# ---------------- 领地详情：聚合 ----------------
def owner_overview(cells: list[TerritoryCell]) -> dict:
    """详情页用的聚合：形状格子 / 活力 / 守了多久。只给格子归属，不给轨迹。"""
    cells = [c for c in cells if c.owner_id]
    total = len(cells)
    if not total:
        return {"cells": [], "cells_total": 0, "vitality_avg": 0.0, "days_left": 0,
                "first_claimed_at": None, "held_days": 0}
    vit = sum(float(c.vitality or 0) for c in cells) / total
    first = min((c.first_claimed_at for c in cells if c.first_claimed_at), default=None)
    now = datetime.now()
    return {
        "cells": [[c.cell_key, round(float(c.vitality or 0))] for c in cells[:4000]],
        "cells_total": total,
        "vitality_avg": round(vit, 1),
        "days_left": max(0, math.ceil(vit / DECAY_PER_DAY)),
        "first_claimed_at": first.isoformat() if first else None,
        "held_days": max(0, (now - first).days) if first else 0,
    }


def head_to_head(db: Session, viewer_id: int, owner_id: int) -> dict:
    """我和 TA 的交锋战绩：按「场」算，同一场切很多格只记 1 次。"""
    if viewer_id == owner_id:
        return {"wins": 0, "losses": 0}

    def battles(actor: int, victim: int) -> int:
        # run_id 为空的历史事件用 -id 兜底，保证每条仍算一场
        units = func.count(func.distinct(
            func.coalesce(TerritoryEvent.run_id, -TerritoryEvent.id)))
        row = (db.query(units)
               .filter(TerritoryEvent.actor_id == actor,
                       TerritoryEvent.victim_id == victim,
                       TerritoryEvent.type.in_(("cut", "steal")))
               .scalar())
        return int(row or 0)

    return {"wins": battles(viewer_id, owner_id), "losses": battles(owner_id, viewer_id)}


# ---------------- 跑完整屏结算 ----------------
def settlement(db: Session, user: User, workout_id: int) -> dict | None:
    """一次跑步的完整结算：本场拿了什么 + 从谁手里拿的 + 现在排第几 / 什么段位。

    「跑完那一刻」是整个玩法的情绪最高点，所以这里要给足信息，
    不能只留一行 +1.05 万㎡。
    """
    run = (db.query(TerritoryRun)
           .filter(TerritoryRun.workout_id == workout_id,
                   TerritoryRun.user_id == user.id)
           .first())
    if not run:
        return None
    base = _run_dict(run)

    victims = []
    if run.id:
        rows = (db.query(TerritoryEvent.victim_id, func.count(TerritoryEvent.id))
                .filter(TerritoryEvent.run_id == run.id,
                        TerritoryEvent.type.in_(("cut", "steal", "recapture")))
                .group_by(TerritoryEvent.victim_id).all())
        for vid, n in rows:
            if not vid:
                continue
            victims.append({"user_id": vid, "nickname": _nick(db, vid),
                            "cells": int(n),
                            "area_m2": int(n) * CELL_M * CELL_M})
        victims.sort(key=lambda v: -v["cells"])

    area_cells = int((db.query(func.count(TerritoryCell.id))
                      .filter(TerritoryCell.owner_id == user.id).scalar()) or 0)
    area_total = area_cells * CELL_M * CELL_M
    gap = rank_gap(db, user.id)
    return {
        **base,
        "area_total_m2": area_total,
        "cells_total": area_cells,
        "victims": victims,
        "tier": tier_of(area_total),
        "rank": gap["rank"],
        "rank_of": gap["rank_of"],
        "ahead_name": gap["ahead_name"],
        "ahead_gap_m2": gap["ahead_gap_m2"],
        "recapture_cells": len(stolen_from_me(db, user.id)),
    }


# ---------------- 下战书 ----------------
def _nick(db: Session, user_id: int) -> str:
    u = db.get(User, user_id)
    return (u.nickname or u.username) if u else ""


def _challenge_dict(ch: TerritoryChallenge, viewer_id: int, names: dict[int, str]) -> dict:
    outgoing = ch.challenger_id == viewer_id
    other = ch.target_id if outgoing else ch.challenger_id
    left = 0
    if ch.expires_at:
        left = max(0, int((ch.expires_at - datetime.now()).total_seconds()))
    return {
        "id": ch.id,
        "direction": "outgoing" if outgoing else "incoming",
        "status": ch.status,
        "other_id": other,
        "other_name": names.get(other, ""),
        "seconds_left": left,
        "accepted": bool(ch.accepted_at),
        "created_at": ch.created_at.isoformat() if ch.created_at else None,
        "expires_at": ch.expires_at.isoformat() if ch.expires_at else None,
    }


def _resolve(db: Session, ch: TerritoryChallenge, now: datetime) -> bool:
    """到期结算：24h 内挑战者真的切走过对方的地 → 赢，否则算对方守住。"""
    if ch.status != "pending" or not ch.expires_at or now < ch.expires_at:
        return False
    hit = (db.query(TerritoryEvent.id)
           .filter(TerritoryEvent.actor_id == ch.challenger_id,
                   TerritoryEvent.victim_id == ch.target_id,
                   TerritoryEvent.type.in_(("cut", "steal")),
                   TerritoryEvent.created_at >= ch.created_at,
                   TerritoryEvent.created_at <= ch.expires_at)
           .first())
    ch.status = "win" if hit else "lose"
    ch.resolved_at = now
    return True


def resolve_challenges(db: Session, user_id: int | None = None) -> None:
    q = db.query(TerritoryChallenge).filter(TerritoryChallenge.status == "pending")
    if user_id:
        q = q.filter(or_(TerritoryChallenge.challenger_id == user_id,
                         TerritoryChallenge.target_id == user_id))
    now = datetime.now()
    changed = False
    for ch in q.limit(200).all():
        if _resolve(db, ch, now):
            changed = True
    if changed:
        db.commit()


def active_challenge(db: Session, viewer_id: int, other_id: int) -> dict | None:
    """我和 TA 之间还没结束的战书（我下的或 TA 下的）。"""
    resolve_challenges(db, viewer_id)
    ch = (db.query(TerritoryChallenge)
          .filter(TerritoryChallenge.status == "pending",
                  or_(and_(TerritoryChallenge.challenger_id == viewer_id,
                           TerritoryChallenge.target_id == other_id),
                      and_(TerritoryChallenge.challenger_id == other_id,
                           TerritoryChallenge.target_id == viewer_id)))
          .order_by(TerritoryChallenge.id.desc()).first())
    if not ch:
        return None
    return _challenge_dict(ch, viewer_id, {other_id: _nick(db, other_id)})


def create_challenge(db: Session, challenger: User, target_id: int) -> dict:
    if target_id == challenger.id:
        raise ValueError("不能给自己下战书")
    if target_id not in friend_ids(db, challenger.id):
        raise ValueError("先加好友才能下战书")
    if not db.get(User, target_id):
        raise ValueError("对方不存在")
    existed = (db.query(TerritoryChallenge)
               .filter(TerritoryChallenge.status == "pending",
                       TerritoryChallenge.challenger_id == challenger.id,
                       TerritoryChallenge.target_id == target_id)
               .first())
    if existed:
        return _challenge_dict(existed, challenger.id, {target_id: _nick(db, target_id)})
    count = (db.query(func.count(TerritoryCell.id))
             .filter(TerritoryCell.owner_id == target_id).scalar()) or 0
    now = datetime.now()
    ch = TerritoryChallenge(
        challenger_id=challenger.id, target_id=target_id, status="pending",
        snapshot_area=float(count) * CELL_M * CELL_M,
        created_at=now, expires_at=now + timedelta(hours=CHALLENGE_HOURS),
    )
    db.add(ch)
    db.commit()
    return _challenge_dict(ch, challenger.id, {target_id: _nick(db, target_id)})


def accept_challenge(db: Session, user: User, challenge_id: int) -> dict:
    ch = db.get(TerritoryChallenge, challenge_id)
    if not ch or ch.target_id != user.id:
        raise ValueError("战书不存在")
    if ch.status != "pending":
        raise ValueError("这封战书已经结束了")
    ch.accepted_at = datetime.now()
    db.commit()
    return _challenge_dict(ch, user.id, {ch.challenger_id: _nick(db, ch.challenger_id)})


# ---------------- 演示种子 ----------------
def _stadium_track(center_lat: float, center_lng: float,
                   half_len_m: float = 42.195, radius_m: float = 36.5,
                   n: int = 80, rotation_deg: float = 0.0) -> list[dict]:
    pts: list[dict] = []
    rad = math.radians(rotation_deg)
    cos_r, sin_r = math.cos(rad), math.sin(rad)

    def add(x: float, y: float) -> None:
        xr = x * cos_r - y * sin_r
        yr = x * sin_r + y * cos_r
        pts.append({"lat": center_lat + yr / 111_320.0,
                    "lng": center_lng + xr / (111_320.0 * math.cos(math.radians(center_lat))),
                    "t": len(pts) * 2, "acc": 6})

    for i in range(n):
        add(half_len_m - 2 * half_len_m * i / n, -radius_m)
    for i in range(1, 40):
        a = -math.pi / 2 - math.pi * i / 40
        add(-half_len_m + radius_m * math.cos(a), radius_m * math.sin(a))
    for i in range(n):
        add(-half_len_m + 2 * half_len_m * i / n, radius_m)
    for i in range(1, 41):
        a = math.pi / 2 - math.pi * i / 40
        add(half_len_m + radius_m * math.cos(a), radius_m * math.sin(a))
    return pts


DEMO_SCHOOL = (28.19786, 112.86688)   # 湖南第一师范学院东方红校区田径场中心（WGS-84）


def _ensure_demo_user(db: Session, username: str, nickname: str,
                      password: str = "demo123456") -> User:
    from app.core.security import hash_password
    u = db.query(User).filter(User.username == username).first()
    if u is None:
        u = User(username=username, nickname=nickname,
                 password_hash=hash_password(password), points_total=120)
        db.add(u)
        db.flush()
    else:
        u.nickname = nickname
        # 演示账号统一密码，方便手机登录；不影响正式用户
        u.password_hash = hash_password(password)
    return u


def _patch_cells(lat: float, lng: float, w: int, h: int) -> list[str]:
    """以某个点为中心，取 w×h 个格子（w/h 为格数，奇数时居中）"""
    cx = math.floor(lng / DLNG)
    cy = math.floor(lat / DLAT)
    x0 = cx - w // 2
    y0 = cy - h // 2
    return [f"t25_{x}_{y}" for x in range(x0, x0 + w) for y in range(y0, y0 + h)]


def _offset_latlng(lat: float, lng: float, dx_m: float, dy_m: float) -> tuple[float, float]:
    return (lat + dy_m / 111_320.0,
            lng + dx_m / (111_320.0 * math.cos(math.radians(lat))))


def seed_demo(db: Session, reset: bool = False) -> dict:
    """学校操场演示数据：

    - sgx 占 400m 操场（含内场）
    - 另外 9 个账号在操场周围占食堂/篮球场/教学楼等块
    - sgx 自动加其中 5 个为好友，地图「好友」筛选能看出区别
    """
    if reset:
        db.query(TerritoryCell).delete()
        db.query(TerritoryEvent).delete()
        db.query(TerritoryRun).delete()
        db.query(UserFriend).delete()
        db.commit()
    if db.query(TerritoryCell).count() > 0:
        return {"skipped": True}

    now = datetime.now()
    old = now - timedelta(hours=2)
    center_lat, center_lng = DEMO_SCHOOL

    # 1) 账号：已有 1–5 + sgx 6，再补 4 个
    users: dict[str, User] = {}
    for uid, username, nick in [
        (1, "demo_xiaoming", "小明"), (2, "demo_xiaohong", "小红"),
        (3, "demo_fitness", "健身达人Leo"), (4, "demo_yoga", "瑜伽酱"),
        (5, "demo_newbie", "养生新人"),
    ]:
        u = db.get(User, uid)
        if u:
            u.nickname = nick
            users[nick] = u
    sgx = _ensure_demo_user(db, "sgx", "sgx")
    users["sgx"] = sgx
    for username, nick in [("demo_ajie", "阿杰"), ("demo_xiaoyu", "小雨"),
                           ("demo_zihan", "子涵"), ("demo_dapeng", "大鹏")]:
        users[nick] = _ensure_demo_user(db, username, nick)
    # 一个还没有领地的朋友：用来演示「不是每个人都有地」
    users["小北"] = _ensure_demo_user(db, "demo_xiaobei", "小北")

    colors = {
        "sgx": "#2563eb", "小明": "#f97316", "小红": "#10b981",
        "健身达人Leo": "#8b5cf6", "瑜伽酱": "#ef4444", "养生新人": "#06b6d4",
        "阿杰": "#ec4899", "小雨": "#14b8a6", "子涵": "#6366f1", "大鹏": "#eab308",
        "小北": "#94a3b8",
    }
    for nick, u in users.items():
        pref = get_pref(db, u.id)
        pref.enabled = True
        pref.color = colors.get(nick)

    created = 0
    cut_count = 0
    seen: set[str] = set()

    def add_cell(key: str, uid: int, vitality: float, force: bool = False) -> None:
        nonlocal created, cut_count
        if key in seen and not force:
            return
        seen.add(key)
        existed = db.query(TerritoryCell).filter(TerritoryCell.cell_key == key).first()
        if existed:
            if force and existed.owner_id and existed.owner_id != uid:
                existed.prev_owner_id = existed.owner_id
                existed.owner_id = uid
                existed.vitality = vitality
                existed.state = "owned"
                existed.owner_changed_at = old
                existed.stolen_at = old
                existed.last_visit_at = old
                existed.last_decay_date = date.today()
                cut_count += 1
            return
        ix, iy = key_ixiy(key)
        db.add(TerritoryCell(
            cell_key=key, ix=ix, iy=iy, owner_id=uid, vitality=vitality, state="owned",
            first_claimed_at=old, last_visit_at=old, last_decay_date=date.today(),
            owner_changed_at=old,
        ))
        db.flush()
        created += 1

    # 2) sgx 的 400m 操场：闭环，跑道 + 内场都归自己（不再是空心圈）
    track = _stadium_track(center_lat, center_lng, half_len_m=42.195, radius_m=36.5,
                           n=80, rotation_deg=103)
    trace = rasterize(track)
    fill = fill_interior(trace)
    for key in trace:
        add_cell(key, sgx.id, 100.0)
    for key in fill:
        add_cell(key, sgx.id, 78.0)

    # 给 sgx 补一条真实闭合轨迹，地图上能看到「跑过的线」
    if reset:
        db.query(Workout).filter(Workout.client_id.like("seed-%")).delete()
    if not db.query(Workout).filter(Workout.client_id == "seed-sgx-loop").first():
        demo_w = Workout(
            user_id=sgx.id, sport_type="跑步", source="gps",
            start_at=old, end_at=old + timedelta(minutes=4),
            duration_sec=240, distance_m=400, avg_pace_sec_km=600,
            calories=42, intensity="moderate", met_used=7.0,
            track=track, client_id="seed-sgx-loop",
            note="学校操场演示轨迹",
        )
        db.add(demo_w)
        db.flush()
        db.add(TerritoryRun(
            workout_id=demo_w.id, user_id=sgx.id, valid=True, reason="ok_loop",
            cells_new=len(trace) + len(fill), cells_filled=len(fill), loop=True,
            cells=trace + fill, created_at=old,
        ))

    # 3) 周围用户各自跑一个闭环（不是手放矩形），形状更自然
    loops = [
        ("小明", 150, 90, 38, 26, 20, 82),
        ("小红", 20, 0, 35, 26, 103, 65),
        ("健身达人Leo", 100, -140, 32, 22, 0, 50),
        ("瑜伽酱", -110, -140, 34, 24, 45, 38),
        ("养生新人", -170, 0, 32, 22, 80, 28),
        ("阿杰", -140, 120, 30, 20, 20, 22),
        ("小雨", 0, 155, 32, 22, 10, 16),
        ("子涵", 30, -180, 30, 20, 60, 12),
        ("大鹏", 180, 50, 28, 20, 15, 8),
    ]
    for idx, (nick, dx, dy, half, radius, rot, vit) in enumerate(loops):
        u = users[nick]
        lat, lng = _offset_latlng(center_lat, center_lng, dx, dy)
        demo_track = _stadium_track(lat, lng, half_len_m=half, radius_m=radius,
                                    n=60, rotation_deg=rot)
        tr = rasterize(demo_track)
        fl = fill_interior(tr)
        for key in tr:
            add_cell(key, u.id, float(vit))
        for key in fl:
            add_cell(key, u.id, float(max(8.0, vit * 0.8)))
        # 小红从 sgx 的东侧切走一块：演示「好友抢地」
        if nick == "小红":
            for key in fl:
                add_cell(key, u.id, float(vit), force=True)

        # 每个闭环都有一条对应的跑步记录，点开领地能看到时长/配速/消耗
        length = max(120.0, track_length_m(demo_track))
        duration = max(240, int(length / 2.2))
        run_at = old + timedelta(minutes=idx * 6)
        w = Workout(
            user_id=u.id, sport_type="跑步", source="gps",
            start_at=run_at, end_at=run_at + timedelta(seconds=duration),
            duration_sec=duration, distance_m=round(length, 1),
            avg_pace_sec_km=round(duration / (length / 1000), 1),
            calories=round(length * 0.06, 1), intensity="moderate", met_used=7.0,
            track=demo_track, client_id=f"seed-{u.id}-loop",
            note="学校操场演示轨迹",
        )
        db.add(w)
        db.flush()
        db.add(TerritoryRun(
            workout_id=w.id, user_id=u.id, valid=True, reason="ok_loop",
            cells_new=len(tr) + len(fl), cells_filled=len(fl),
            cells_cut=(cut_count if nick == "小红" else 0),
            loop=True, cells=tr + fl, created_at=run_at,
        ))

    if cut_count:
        db.add(TerritoryEvent(type="cut", cell_key="*", actor_id=users["小红"].id,
                              victim_id=sgx.id, created_at=old,
                              meta={"cells": cut_count,
                                    "area_m2": cut_count * CELL_M * CELL_M}))

    # 4) sgx 的好友：小明、小红、瑜伽酱、阿杰、小雨；Leo/养生新人/子涵/大鹏保持“附近的人”
    friend_names = ["小明", "小红", "瑜伽酱", "阿杰", "小雨", "小北"]
    for nick in friend_names:
        fid = users[nick].id
        if not db.query(UserFriend).filter(UserFriend.user_id == sgx.id,
                                           UserFriend.friend_id == fid).first():
            db.add(UserFriend(user_id=sgx.id, friend_id=fid, created_at=now))

    db.commit()
    return {
        "created": created,
        "users": {nick: u.id for nick, u in users.items()},
        "center": [center_lat, center_lng],
        "friends": [users[n].id for n in friend_names],
    }
