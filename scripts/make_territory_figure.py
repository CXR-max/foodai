"""生成领地跑「网格 vs 平滑轮廓 vs 活力衰减」示意图

用途：docs/运动板块-领地跑玩法与实现方案.md 的 2.5 节配图。
运行：backend\\.venv\\Scripts\\python.exe scripts\\make_territory_figure.py
输出：docs/figures/territory-render-mock.png
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

W, H = 1460, 660
BG = (247, 249, 252)

FONT_PATH = "C:/Windows/Fonts/msyh.ttc"
FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_PATH, size)


# ---------------- 模拟一块领地：16×12 格 ----------------
RUN = {(x, y) for x in range(3, 13) for y in (2, 8)}          # 上下两边
RUN |= {(x, y) for y in range(2, 9) for x in (3, 12)}          # 左右两边
FILL = {(x, y) for x in range(4, 12) for y in range(3, 8)}     # 围住的内场
ORANGE = {(x, y) for x in range(13, 16) for y in range(2, 6)}  # 另一位玩家的地

BLUE = (59, 130, 246)
BLUE_LIGHT = (147, 197, 253)
BLUE_DARK = (37, 99, 235)
ORANGE_C = (249, 115, 22)

CELL = 24
OX, OY = 36, 104


def px(x: int, y: int) -> tuple[int, int]:
    return OX + x * CELL, OY + y * CELL


def draw_grid_panel(d: ImageDraw.ImageDraw) -> None:
    for x, y in sorted(FILL):
        x0, y0 = px(x, y)
        d.rectangle([x0 + 1, y0 + 1, x0 + CELL - 2, y0 + CELL - 2], fill=BLUE_LIGHT)
    for x, y in sorted(RUN):
        x0, y0 = px(x, y)
        d.rounded_rectangle([x0 + 1, y0 + 1, x0 + CELL - 2, y0 + CELL - 2],
                            radius=4, fill=BLUE)
    for x, y in sorted(ORANGE):
        x0, y0 = px(x, y)
        d.rounded_rectangle([x0 + 1, y0 + 1, x0 + CELL - 2, y0 + CELL - 2],
                            radius=4, fill=ORANGE_C)
    # 真实轨迹：沿外圈画一条虚线
    pts = [(3, 2), (13, 2), (13, 9), (3, 9), (3, 2)]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        _dashed(d, [(x1, y1), (x2, y2)], (22, 163, 74), width=3, dash=8)


def draw_smooth_panel(d: ImageDraw.ImageDraw) -> None:
    # 无格线：整块同色，只画外轮廓
    for x, y in sorted(FILL):
        x0, y0 = px(x, y)
        d.rectangle([x0, y0, x0 + CELL - 1, y0 + CELL - 1], fill=BLUE)
    for x, y in sorted(RUN):
        x0, y0 = px(x, y)
        d.rectangle([x0, y0, x0 + CELL - 1, y0 + CELL - 1], fill=BLUE)
    for x, y in sorted(ORANGE):
        x0, y0 = px(x, y)
        d.rectangle([x0, y0, x0 + CELL - 1, y0 + CELL - 1], fill=ORANGE_C)
    _outline(d, RUN | FILL, BLUE_DARK, width=4)
    _outline(d, ORANGE, (194, 65, 12), width=4)
    pts = [(3, 2), (13, 2), (13, 9), (3, 9), (3, 2)]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        _dashed(d, [(x1, y1), (x2, y2)], (22, 163, 74), width=3, dash=8)


def _outline(d: ImageDraw.ImageDraw, cells: set[tuple[int, int]],
             color: tuple[int, int, int], width: int = 3) -> None:
    """只画与外部相邻的边，合并成一块地的外轮廓"""
    for x, y in cells:
        x0, y0 = px(x, y)
        x1, y1 = x0 + CELL, y0 + CELL
        if (x, y - 1) not in cells:
            d.line([x0, y0, x1, y0], fill=color, width=width)
        if (x, y + 1) not in cells:
            d.line([x0, y1, x1, y1], fill=color, width=width)
        if (x - 1, y) not in cells:
            d.line([x0, y0, x0, y1], fill=color, width=width)
        if (x + 1, y) not in cells:
            d.line([x1, y0, x1, y1], fill=color, width=width)


def _dashed(d: ImageDraw.ImageDraw, pts, color, width=3, dash=8) -> None:
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        ax, ay = px(x1, y1)
        bx, by = px(x2, y2)
        length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        if length == 0:
            continue
        n = int(length // dash)
        for i in range(n):
            if i % 2:
                continue
            t1, t2 = i / n, (i + 0.7) / n
            d.line([ax + (bx - ax) * t1, ay + (by - ay) * t1,
                    ax + (bx - ax) * t2, ay + (by - ay) * t2],
                   fill=color, width=width)


def draw_decay_panel(d: ImageDraw.ImageDraw, x0: int, y0: int) -> None:
    """同一块地：第 1 / 5 / 9 天，颜色逐渐变淡"""
    stages = [(1.0, "第 1 天"), (0.58, "第 5 天"), (0.22, "第 9 天")]
    cell = 9
    for i, (alpha, label) in enumerate(stages):
        oy = y0 + i * 108
        # 形状底
        for x, y in FILL:
            d.rectangle([x0 + x * cell, oy + y * cell,
                         x0 + (x + 1) * cell - 1, oy + (y + 1) * cell - 1],
                        fill=_blend(BLUE, BG, alpha))
        for x, y in RUN:
            d.rectangle([x0 + x * cell, oy + y * cell,
                         x0 + (x + 1) * cell - 1, oy + (y + 1) * cell - 1],
                        fill=_blend(BLUE, BG, alpha))
        for x, y in ORANGE:
            d.rectangle([x0 + x * cell, oy + y * cell,
                         x0 + (x + 1) * cell - 1, oy + (y + 1) * cell - 1],
                        fill=_blend(ORANGE_C, BG, alpha))
        d.text((x0 - 4, oy + 30), label, font=font(13), fill=(90, 100, 115), anchor="ra")
        d.text((x0 - 4, oy + 50), f"{int(alpha * 100)} 活力", font=font(12),
               fill=(140, 148, 160), anchor="ra")


def _blend(fg, bg, a: float) -> tuple[int, int, int]:
    return tuple(int(fg[i] * a + bg[i] * (1 - a)) for i in range(3))


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.text((36, 26), "同一块地，三种画法", font=font(26, True), fill=(24, 32, 44))
    d.text((36, 62), "后台都是 25m 格子；用户看到的可以是方格、平滑地块，或者随时间变淡的地块。",
           font=font(14), fill=(100, 110, 125))

    # 标题
    d.text((36, 86), "① 后台数据：格子", font=font(15, True), fill=(55, 65, 81))
    d.text((510, 86), "② 前端展示：合并 + 描边，不画格线", font=font(15, True), fill=(55, 65, 81))
    d.text((1010, 86), "③ 活力衰减：越不跑越淡", font=font(15, True), fill=(55, 65, 81))

    draw_grid_panel(d)

    # 第二块画布：复制一份，改原点
    global OX, OY
    old = (OX, OY)
    OX, OY = 510, 104
    draw_smooth_panel(d)
    OX, OY = old

    draw_decay_panel(d, 1080, 118)

    # 图例
    ly = 500
    d.rounded_rectangle([36, ly - 10, 1420, ly + 66], radius=12, fill=(255, 255, 255))
    d.rounded_rectangle([56, ly + 10, 80, ly + 30], radius=4, fill=BLUE)
    d.text((88, ly + 10), "跑过的格 / 地盘", font=font(13), fill=(70, 80, 95))
    d.rounded_rectangle([220, ly + 10, 244, ly + 30], radius=4, fill=BLUE_LIGHT)
    d.text((252, ly + 10), "闭环围住的内场", font=font(13), fill=(70, 80, 95))
    d.line([386, ly + 20, 426, ly + 20], fill=(22, 163, 74), width=3)
    d.text((434, ly + 10), "真实轨迹（只做圈地动画/描边）", font=font(13), fill=(70, 80, 95))
    d.rounded_rectangle([716, ly + 10, 740, ly + 30], radius=4, fill=ORANGE_C)
    d.text((748, ly + 10), "另一位玩家", font=font(13), fill=(70, 80, 95))
    d.text((920, ly + 10), "颜色深浅 = 活力；无格线 = 看起来像手绘地盘",
           font=font(13), fill=(70, 80, 95))

    d.text((36, 600), "示意数据：16×12 格；实际 25m/格，一圈操场约 28 格。",
           font=font(12), fill=(150, 158, 170))

    img.save("docs/figures/territory-render-mock.png")
    print("saved docs/figures/territory-render-mock.png")


if __name__ == "__main__":
    main()
