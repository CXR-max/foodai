"""运动轨迹计算（距离 / 配速 / 分段 / 强度档）

为什么放在后端：
前端只负责采集点，所有汇总值由这里重算，保证「同一条记录在任何设备上显示一致」，
也避免前端改个数字就能刷里程。

轨迹点格式（前端按此提交）：
    {"lat": 31.23, "lng": 121.47, "t": 128.5, "acc": 6.0}
    t = 从开始到该点的**有效运动秒数**（暂停期间不递增），这样分段配速不会被暂停时间污染
"""
from __future__ import annotations

import math

EARTH_R_M = 6371000.0

# 过滤阈值：手机 GPS 在城市里精度常在 5~20m，超过 35m 的点基本是基站/漂移，直接丢
MAX_ACCURACY_M = 35.0
# 相邻两点距离小于 3m 视为站立抖动
MIN_MOVE_M = 3.0
# 人类跑步不可能超过 12m/s（约 2'20"/km），超过判为漂移
MAX_SPEED_MPS = 12.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """两点球面距离（米）"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


def filter_points(points: list[dict]) -> list[dict]:
    """去噪：精度差 / 站立抖动 / 速度突变 三类点丢弃

    这是轨迹能不能看的关键 —— 不过滤的原始 GPS 画出来是一团毛线，
    距离也会虚高 20%~50%。
    """
    kept: list[dict] = []
    for p in points or []:
        try:
            lat, lng = float(p["lat"]), float(p["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        acc = p.get("acc")
        if acc is not None and float(acc) > MAX_ACCURACY_M:
            continue
        cur = {"lat": lat, "lng": lng, "t": float(p.get("t") or 0), "acc": acc}
        if not kept:
            kept.append(cur)
            continue

        last = kept[-1]
        d = haversine_m(last["lat"], last["lng"], lat, lng)
        dt = cur["t"] - last["t"]

        # 位移太小的点：可能是抖动，也可能是在原地停着；都不累加、不替换基准点
        if d < MIN_MOVE_M:
            continue
        # 速度突变：dt 为 0 但位移很大，或算出来超过人类极限
        if dt <= 0:
            continue
        if d / dt > MAX_SPEED_MPS:
            continue
        kept.append(cur)
    return kept


def compute_track(points: list[dict]) -> dict:
    """根据去噪后的点算 距离 / 分段 / 最快公里配速 / 有效时长"""
    if not points:
        return {"distance_m": 0.0, "splits": [], "best_pace_sec_km": None,
                "duration_sec": 0, "elevation_gain": 0.0}

    total = 0.0
    cum: list[tuple[float, float]] = []   # (累计距离, 该点时间)
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        total += haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
        cum.append((total, b["t"]))

    # 每满 1km 落一个分段：用线性插值求"到达第 k 公里"的时刻
    splits: list[dict] = []
    k = 1
    prev_t = 0.0
    prev_d = 0.0
    for dist, t in cum:
        while dist >= k * 1000:
            # 在该段内插值出跨过 k 公里的时间点
            span = dist - prev_d
            ratio = ((k * 1000 - prev_d) / span) if span > 0 else 1.0
            cross_t = prev_t + (t - prev_t) * max(0.0, min(1.0, ratio))
            sec = round(cross_t - (prev_t if splits else 0), 1)
            splits.append({
                "km": k,
                "sec": sec,
                "pace_sec_km": round(sec, 1),
            })
            prev_t = cross_t
            prev_d = float(k * 1000)
            k += 1

    # 最后一截不足 1km 也记一段（Keep 报告里会有"最后一公里"）
    if total - prev_d >= 50:
        t_end = cum[-1][1] if cum else 0.0
        sec = round(t_end - prev_t, 1)
        if sec > 0:
            splits.append({
                "km": round(total / 1000.0, 2),
                "sec": sec,
                "pace_sec_km": round(sec / max((total - prev_d) / 1000.0, 0.001), 1),
                "partial": True,
            })

    full = [s["pace_sec_km"] for s in splits if not s.get("partial")]
    return {
        "distance_m": round(total, 1),
        "splits": splits,
        "best_pace_sec_km": min(full) if full else None,
        "duration_sec": int(round(cum[-1][1]) if cum else 0),
        "elevation_gain": 0.0,
    }


def estimate_intensity(sport_type: str, distance_m: float, duration_sec: int) -> str:
    """按配速/速度估强度档，用于喂给现有的个性化消耗模型

    这样跑步记录的消耗仍然走 MET × 体重 × 时长 × 个体系数，
    配速只是把"强度档"选对，口径和手动打卡完全一致。
    """
    if duration_sec <= 0 or distance_m <= 0:
        return "moderate"
    if sport_type in ("骑行",):
        speed_kmh = (distance_m / 1000.0) / (duration_sec / 3600.0)
        if speed_kmh >= 20:
            return "intense"
        if speed_kmh >= 13:
            return "moderate"
        return "light"

    pace = duration_sec / (distance_m / 1000.0)      # 秒/公里
    if sport_type in ("快走", "散步"):
        if pace <= 480:      # 8'00"/km 以内
            return "intense"
        if pace <= 600:
            return "moderate"
        return "light"
    # 跑步类
    if pace <= 330:          # 5'30"/km 以内
        return "intense"
    if pace <= 420:          # 7'00"/km 以内
        return "moderate"
    return "light"


# 我们的消耗模型里没有"跑步机"，按跑步算；跳绳本来就有
MET_TYPE_MAP = {"跑步机": "跑步"}
