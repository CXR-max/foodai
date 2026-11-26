"""领地跑（Territory Run）核心算法离线验证脚本

不需要数据库、不需要前端，纯标准库。目的：在动手写表之前，先把
「格子怎么切、轨迹怎么落格、闭环怎么填、衰减/抢夺数值是否合理」
跑一遍，确认规则不是拍脑袋。

运行：
    python scripts/territory_sim.py

输出：
    1) 15 / 20 / 25m 三种格子下：400m 操场一圈落格 / 内场填充
    2) 2km 校区大圈 → 被「内场最远 60m」规则拦下（不能一圈吞校区）
    3) 同一路线跑两次（不同 GPS 噪声）→ 落格重合度（Jaccard，检验抖不抖）
    4) 抢夺 / 夺回 / 衰减的数值推演
"""
from __future__ import annotations

import math
import random
from collections import Counter, deque


class Grid:
    """WGS-84 等经纬度网格（一定要在 WGS-84 上切，渲染时再转 GCJ-02）"""

    def __init__(self, cell_m: float = 25.0, lat0: float = 31.2300, lng0: float = 121.4700):
        self.cell_m = cell_m
        self.lat0, self.lng0 = lat0, lng0
        self.dlat = cell_m / 111_320.0
        self.dlng = cell_m / (111_320.0 * math.cos(math.radians(lat0)))

    def to_xy(self, lat: float, lng: float) -> tuple[float, float]:
        return (lng - self.lng0) / self.dlng, (lat - self.lat0) / self.dlat

    def to_latlng(self, x: float, y: float) -> tuple[float, float]:
        return self.lat0 + y * self.dlat, self.lng0 + x * self.dlng

    def cell_of(self, lat: float, lng: float) -> tuple[int, int]:
        x, y = self.to_xy(lat, lng)
        return math.floor(x), math.floor(y)

    def center(self, ix: int, iy: int) -> tuple[float, float]:
        return self.to_latlng(ix + 0.5, iy + 0.5)


def dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1 = a
    lat2, lng2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000.0 * math.asin(math.sqrt(h))


# ---------------- 轨迹生成 ----------------
def stadium_track(g: Grid, noise_m: float = 4.0, seed: int = 7) -> list[tuple[float, float]]:
    """标准 400m 跑道：直道 84.39m + 弯道半径 36.5m，首尾闭合"""
    rng = random.Random(seed)
    half, r = 84.39 / 2, 36.5
    pts: list[tuple[float, float]] = []

    def add(x: float, y: float):
        x += rng.gauss(0, noise_m)
        y += rng.gauss(0, noise_m)
        pts.append(g.to_latlng(x / g.cell_m, y / g.cell_m))

    n = 50
    for i in range(n):                                   # 下直道 右→左
        add(half - 2 * half * i / n, -r)
    for i in range(1, 40):                               # 左弯道 下→上
        a = -math.pi / 2 - math.pi * i / 40
        add(-half + r * math.cos(a), r * math.sin(a))
    for i in range(n):                                   # 上直道 左→右
        add(-half + 2 * half * i / n, r)
    for i in range(1, 41):                               # 右弯道 上→下
        a = math.pi / 2 - math.pi * i / 40
        add(half + r * math.cos(a), r * math.sin(a))
    return pts


def campus_loop(g: Grid, w: float = 500.0, h: float = 300.0, noise_m: float = 4.0,
                seed: int = 11) -> list[tuple[float, float]]:
    """校区大圈：500 × 300m，周长 1600m"""
    rng = random.Random(seed)
    pts: list[tuple[float, float]] = []
    step = 3.0

    def add(x: float, y: float):
        x += rng.gauss(0, noise_m)
        y += rng.gauss(0, noise_m)
        pts.append(g.to_latlng(x / g.cell_m, y / g.cell_m))

    corners = [(0, 0), (w, 0), (w, h), (0, h), (0, 0)]
    for (x1, y1), (x2, y2) in zip(corners, corners[1:]):
        n = max(1, int(math.hypot(x2 - x1, y2 - y1) / step))
        for i in range(n):
            add(x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n)
    return pts


# ---------------- 落格 ----------------
def rasterize(g: Grid, pts: list[tuple[float, float]], step_m: float = 3.0,
              min_samples: int = 2) -> set[tuple[int, int]]:
    """线段每 3m 采样；同一格至少 2 个采样点才算（防擦角）

    刻意不做「内缩边距」：边距会让占地结果依赖网格原点这种任意选择，
    且会把正好压在网格线上的路整条判掉。代价是压线跑可能占两列，
    由「每次跑步总格数上限」兜底。
    """
    cnt: Counter[tuple[int, int]] = Counter()
    for (lat1, lng1), (lat2, lng2) in zip(pts, pts[1:]):
        x1, y1 = g.to_xy(lat1, lng1)
        x2, y2 = g.to_xy(lat2, lng2)
        seg = math.hypot(x2 - x1, y2 - y1) * g.cell_m
        n = max(1, int(seg / step_m))
        for i in range(n + 1):
            t = i / n
            x, y = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            cnt[(math.floor(x), math.floor(y))] += 1
    return {c for c, k in cnt.items() if k >= min_samples}


# ---------------- 闭环填充 ----------------
def close_loop(pts: list[tuple[float, float]], tol_m: float = 30.0) -> bool:
    return len(pts) >= 3 and dist_m(pts[0], pts[-1]) <= tol_m


def bridge_gaps(cells: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """把墙上 1 格宽的缺口补上（GPS 噪声常见），不整体膨胀

    整体膨胀 1 格会把 20m 操场的内场直接吃掉（内场本来就只有 2~4 格），
    所以只补「两端是墙、中间空一格」的位置。
    """
    blocked = set(cells)
    for x, y in cells:
        if (x + 2, y) in cells and (x + 1, y) not in cells:
            blocked.add((x + 1, y))
        if (x, y + 2) in cells and (x, y + 1) not in cells:
            blocked.add((x, y + 1))
    return blocked


def fill_interior(g: Grid, cells: set[tuple[int, int]],
                  max_dist_m: float = 60.0, max_cells: int = 120,
                  min_cells: int = 1) -> tuple[set[tuple[int, int]], str]:
    """轨迹格当墙 → 从包围盒边界泛洪 → 没被淹到的就是内部

    - 墙上 1 格缺口用 bridge_gaps 补，避免「漏水」把整片内场连到外面
    - 内部每格都必须离轨迹 ≤ 60m（取 max 而不是 min）：
      小圈（操场、街区）算圈地，大圈（绕校区）不算
    - 上限 120 格：再大也不给
    """
    if not cells:
        return set(), "empty"
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    x0, x1 = min(xs) - 3, max(xs) + 3
    y0, y1 = min(ys) - 3, max(ys) + 3

    blocked = bridge_gaps(cells)

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
            nx, ny = x + dx, y + dy
            if not (x0 <= nx <= x1 and y0 <= ny <= y1):
                continue
            if (nx, ny) in blocked or (nx, ny) in seen:
                continue
            seen.add((nx, ny)); q.append((nx, ny))

    interior = {(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)
                if (x, y) not in blocked and (x, y) not in seen}
    if not interior:
        return set(), "no_interior"

    # 每格到最近轨迹格中心的距离，再取所有内部格里最远的那个
    centers = [g.center(*c) for c in cells]
    far = max(min(dist_m(g.center(*c), p) for p in centers) for c in interior)
    if far > max_dist_m:
        return set(), f"too_wide({far:.0f}m>{max_dist_m:.0f}m)"
    if len(interior) > max_cells:
        return set(), f"too_big({len(interior)}>{max_cells})"
    if len(interior) < min_cells:
        return set(), f"too_small({len(interior)}<{min_cells})"
    return interior, f"ok({len(interior)})"


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


# ---------------- 数值推演 ----------------
def simulate_capture() -> list[str]:
    lines = ["起始：A 的格子，活力 100"]
    vit = 100.0
    for i in range(1, 5):
        dmg = 45 * 0.6                      # A 24h 内跑过 → 伤害 6 折 = 27
        vit -= dmg
        lines.append(f"  B 第 {i} 次经过：-{dmg:.0f} → {max(vit, 0):.0f}"
                     + ("（易主 B，活力 55）" if vit <= 0 else ""))
        if vit <= 0:
            vit = 55.0
            break
    dmg = 45 * 2                            # 48h 内夺回 → 双倍 = 90
    vit -= dmg
    lines.append(f"A 复仇一次：-{dmg:.0f} → {max(vit, 0):.0f}"
                 + ("（夺回，活力 55）" if vit <= 0 else "（没抢回来）"))
    return lines


def simulate_decay(area: int = 30) -> list[str]:
    tax = min(6, area // 40)
    rate = 8 + tax
    lines = [f"面积 {area} 格 → 基础衰减 {rate}/天（含面积税 {tax}）"]
    vit, day = 100.0, 0
    while vit > 0 and day < 60:
        day += 1
        r = rate + (4 if day > 7 else 0)
        vit = max(0, vit - r)
        if day in (1, 3, 7, 10, 12, 14):
            lines.append(f"  第 {day:2d} 天：{vit:5.1f}（今日 -{r}）")
    lines.append(f"  → 约 {day} 天完全荒废（第 3 天起颜色明显变淡）")
    return lines


def ascii_map(cells: set[tuple[int, int]], fill: set[tuple[int, int]]) -> str:
    allc = cells | fill
    if not allc:
        return ""
    xs = [c[0] for c in allc]
    ys = [c[1] for c in allc]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    out = []
    for gy in range(y1, y0 - 1, -1):
        row = []
        for gx in range(x0, x1 + 1):
            if (gx, gy) in cells:
                row.append("█")
            elif (gx, gy) in fill:
                row.append("·")
            else:
                row.append(" ")
        out.append("".join(row).rstrip())
    return "\n".join(out)


def main() -> None:
    for cell_m in (15.0, 20.0, 25.0):
        g = Grid(cell_m)
        print("=" * 68)
        print(f"【格子 {cell_m:.0f}m】")
        t1 = stadium_track(g, seed=1)
        c1 = rasterize(g, t1)
        f1, why1 = fill_interior(g, c1) if close_loop(t1) else (set(), "not_closed")
        print(f"  400m 操场：落格 {len(c1)} + 内场 {len(f1)} = {len(c1) + len(f1)} 格"
              f"（填充 {why1}，闭环={close_loop(t1)}）")

        vals = [jaccard(c1, rasterize(g, stadium_track(g, seed=s))) for s in range(2, 8)]
        print(f"  同路线换 6 组噪声：平均 Jaccard = {sum(vals) / len(vals):.2f}")

        big = campus_loop(g)
        bc = rasterize(g, big)
        bf, whyb = fill_interior(g, bc) if close_loop(big) else (set(), "not_closed")
        print(f"  2km 校区大圈：落格 {len(bc)} + 内场 {len(bf)}"
              f"（填充 {whyb}，期望被拦）")

    # 用 25m 画一张操场示意
    g = Grid(25.0)
    t = stadium_track(g)
    c = rasterize(g, t)
    f, _ = fill_interior(g, c)
    print("\n" + "=" * 68)
    print("【25m 网格 · 操场一圈示意】█=跑过的格  ·=围住的格")
    print(ascii_map(c, f))

    print("\n" + "=" * 68)
    print("【抢夺 / 夺回】")
    for line in simulate_capture():
        print("  " + line)
    print("\n【衰减】")
    for line in simulate_decay():
        print("  " + line)


if __name__ == "__main__":
    main()
