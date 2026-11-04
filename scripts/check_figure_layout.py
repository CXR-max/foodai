"""检查图里文字是否越界、溢出方框或互相重叠（无图像预览环境下替代肉眼检查）。

用法：backend\\.venv\\Scripts\\python.exe scripts\\check_figure_layout.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "mpl-foodai"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_nutrition_figures as figmod  # noqa: E402

MARGIN = 0.8  # 文字距方框内边的最小留白（数据坐标）


def _capture(builder):
    captured = {}
    real_close = plt.close
    plt.close = lambda f=None: captured.__setitem__("fig", f)
    try:
        builder()
    finally:
        plt.close = real_close
    return captured.get("fig")


def _rects(ax, fig):
    renderer = fig.canvas.get_renderer()
    inv = ax.transData.inverted()
    texts, boxes = [], []
    for art in ax.get_children():
        if isinstance(art, FancyBboxPatch):
            bb = art.get_window_extent(renderer)
            (x0, y0), (x1, y1) = inv.transform([(bb.x0, bb.y0), (bb.x1, bb.y1)])
            boxes.append((x0, y0, x1, y1))
        elif hasattr(art, "get_text") and art.get_text().strip():
            bb = art.get_window_extent(renderer)
            if bb.width <= 0 or bb.height <= 0:
                continue
            (x0, y0), (x1, y1) = inv.transform([(bb.x0, bb.y0), (bb.x1, bb.y1)])
            texts.append((art.get_text().strip().replace("\n", " / "), x0, y0, x1, y1))
    return texts, boxes


def _intersect(a, b, pad=0.0):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 - pad <= bx0 or bx1 - pad <= ax0 or ay1 - pad <= by0 or by1 - pad <= ay0)


def check(name, builder):
    fig = _capture(builder)
    if fig is None:
        print(f"[{name}] 未捕获到 figure")
        return 1

    ax = fig.axes[0]
    fig.canvas.draw()
    texts, boxes = _rects(ax, fig)
    inv = ax.transData.inverted()

    problems = 0
    xlim, ylim = ax.get_xlim(), ax.get_ylim()

    for label, x0, y0, x1, y1 in texts:
        short = label[:34]
        # a) 是否跑出画布
        if x0 < xlim[0] or x1 > xlim[1] or y0 < ylim[0] or y1 > ylim[1]:
            print(f"  [越界] {short!r}")
            problems += 1
            continue

        # b) 找包含文字中心的方框作为宿主
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        hosts = [b for b in boxes if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]]
        if not hosts:
            continue
        host = min(hosts, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        if (x0 < host[0] + MARGIN or x1 > host[2] - MARGIN
                or y0 < host[1] + MARGIN or y1 > host[3] - MARGIN):
            print(f"  [溢出] {short!r} 超出宿主方框 {MARGIN} 留白")
            problems += 1

        # c) 是否压到别的方框
        for b in boxes:
            if b is host or not _intersect((x0, y0, x1, y1), b):
                continue
            inside_b = x0 >= b[0] and x1 <= b[2] and y0 >= b[1] and y1 <= b[3]
            if not inside_b:
                print(f"  [压框] {short!r} 与另一方框相交")
                problems += 1
                break

    # d) 文字两两重叠
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            a, b = texts[i], texts[j]
            if _intersect(a[1:], b[1:], pad=0.4):
                print(f"  [重叠] {a[0][:26]!r} <-> {b[0][:26]!r}")
                problems += 1

    # e) 箭头穿过文字
    arrows = []
    for art in ax.get_children():
        if isinstance(art, FancyArrowPatch):
            path = art.get_path().transformed(art.get_transform())
            bb = path.get_extents()
            (x0, y0), (x1, y1) = inv.transform([(bb.x0, bb.y0), (bb.x1, bb.y1)])
            arrows.append((x0, y0, x1, y1))
    for label, x0, y0, x1, y1 in texts:
        for a in arrows:
            if _intersect((x0, y0, x1, y1), a, pad=0.15):
                print(f"  [箭头穿字] {label[:30]!r}")
                problems += 1
                break

    print(f"[{name}] 文字 {len(texts)} 个 / 方框 {len(boxes)} 个 / 箭头 {len(arrows)} 条 / 问题 {problems} 处")
    return problems


if __name__ == "__main__":
    bad = check("架构图", figmod.figure_architecture)
    bad += check("流程图", figmod.figure_pipeline)
    sys.exit(1 if bad else 0)
