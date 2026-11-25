"""生成人脸登录三张配图（透明底 PNG）"""
import os
from PIL import Image, ImageDraw, ImageFont

S = 3  # 超采样倍数
OUT = r"C:/Users/43120/Desktop/foodai/docs/face-figs"
os.makedirs(OUT, exist_ok=True)

FONT = r"C:/Windows/Fonts/msyh.ttc"
FONTB = r"C:/Windows/Fonts/msyhbd.ttc"

BLUE = (92, 134, 176, 255)
BLUE_L = (201, 216, 230, 255)
BLUE_FILL = (247, 250, 253, 255)
INK = (51, 48, 44, 255)
GRAY = (122, 114, 99, 255)
GRAY_L = (138, 129, 117, 255)
CREAM = (244, 239, 228, 255)


def font(size, bold=False):
    return ImageFont.truetype(FONTB if bold else FONT, size * S, index=0)


def canvas(w, h):
    img = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def rr(d, box, r, fill=None, outline=None, width=1.2, dashed=False):
    x1, y1, x2, y2 = [v * S for v in box]
    if dashed:
        # 手绘虚线圆角框：用弧线+直线拼
        d.rounded_rectangle([x1, y1, x2, y2], radius=r * S, outline=outline,
                            width=max(1, int(width * S)))
        return
    d.rounded_rectangle([x1, y1, x2, y2], radius=r * S, fill=fill,
                        outline=outline, width=max(1, int(width * S)))


def text(d, xy, s, size, color=INK, bold=False, anchor="la"):
    d.text((xy[0] * S, xy[1] * S), s, font=font(size, bold), fill=color, anchor=anchor)


def arrow(d, x1, y1, x2, y2, color=BLUE, width=2.2, head=9):
    d.line([(x1 * S, y1 * S), (x2 * S, y2 * S)], fill=color, width=int(width * S))
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    h = head
    pts = [
        (x2, y2),
        (x2 - h * math.cos(ang - 0.42), y2 - h * math.sin(ang - 0.42)),
        (x2 - h * math.cos(ang + 0.42), y2 - h * math.sin(ang + 0.42)),
    ]
    d.polygon([(p[0] * S, p[1] * S) for p in pts], fill=color)


def badge(d, cx, cy, n, r=17, color=BLUE):
    d.ellipse([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S], fill=color)
    text(d, (cx, cy), str(n), 17, (255, 255, 255, 255), bold=True, anchor="mm")


# ---------------- 图1：怎么用 ----------------
def fig1():
    W, H = 1200, 400
    img, d = canvas(W, H)
    w, h, gap = 230, 120, 46
    y = 110
    xs = [71, 347, 623, 899]
    items = [
        ("密码登录", "首次 · 验证身份"),
        ("绑定人脸", "「我的」→ 绑定"),
        ("输用户名刷脸", "动作检测 + 3 帧"),
        ("进入首页", "不再输密码"),
    ]
    for i, (x, (t, s)) in enumerate(zip(xs, items)):
        rr(d, [x, y, x + w, y + h], 16, fill=BLUE_FILL, outline=BLUE_L)
        badge(d, x + 34, y + 34, i + 1, 16)
        text(d, (x + w / 2, y + 62), t, 25, INK, bold=True, anchor="mm")
        text(d, (x + w / 2, y + 94), s, 18, GRAY, anchor="mm")
        if i < 3:
            arrow(d, x + w + 8, y + h / 2, x + w + gap - 8, y + h / 2)

    # 分隔：只做一次 / 每次登录
    ly = 300
    d.line([(71 * S, ly * S), (393 * S, ly * S)], fill=(206, 196, 180, 255), width=int(1.4 * S))
    text(d, (232, ly + 22), "只设置一次", 18, GRAY_L, anchor="mm")
    d.line([(623 * S, ly * S), (1129 * S, ly * S)], fill=(206, 196, 180, 255), width=int(1.4 * S))
    text(d, (876, ly + 22), "之后每次登录", 18, GRAY_L, anchor="mm")

    img.resize((W, H), Image.LANCZOS).save(os.path.join(OUT, "01-怎么用.png"))
    return "01-怎么用.png"


# ---------------- 图2：怎么做 ----------------
def fig2():
    W, H = 1200, 520
    img, d = canvas(W, H)
    w, h, gap = 210, 130, 25
    y = 80
    xs = [25, 260, 495, 730, 965]
    items = [
        ("摄像头采集", "含动作检测"),
        ("连拍 3 帧", "640 宽 · JPEG"),
        ("多帧校验", "复制图直接拒"),
        ("百度比对", "1:1 · 0~100 分"),
        ("≥80 分发票据", "120 秒 · 一次"),
    ]
    for i, (x, (t, s)) in enumerate(zip(xs, items)):
        hi = i == 4
        rr(d, [x, y, x + w, y + h], 16,
           fill=(235, 245, 239, 255) if hi else BLUE_FILL,
           outline=(157, 200, 176, 255) if hi else BLUE_L)
        badge(d, x + 30, y + 32, i + 1, 15,
              color=(29, 158, 117, 255) if hi else BLUE)
        text(d, (x + w / 2, y + 64), t, 24, INK, bold=True, anchor="mm")
        text(d, (x + w / 2, y + 98), s, 17, GRAY, anchor="mm")
        if i < 4:
            arrow(d, x + w + 2, y + h / 2, x + w + gap - 2, y + h / 2)

    # 底库
    bx, by, bw, bh = 700, 330, 260, 86
    rr(d, [bx, by, bx + bw, by + bh], 14, fill=CREAM, outline=(214, 203, 185, 255))
    text(d, (bx + bw / 2, by + 34), "该账号绑定照片", 19, INK, bold=True, anchor="mm")
    text(d, (bx + bw / 2, by + 62), "data/faces/{user_id}.jpg", 15, GRAY, anchor="mm")

    # 虚线箭头（竖向，指向上方百度比对框）
    import math
    sx, sy = bx + bw / 2, by
    ey = y + h + 6
    for seg in range(0, int(sy - ey), 14):
        y1, y2 = sy - seg, max(ey, sy - seg - 8)
        d.line([(sx * S, y1 * S), (sx * S, y2 * S)], fill=(160, 150, 134, 255),
               width=int(1.6 * S))
    arrow(d, sx, ey + 14, sx, ey, color=(160, 150, 134, 255), head=8)
    text(d, (sx + 12, (sy + ey) / 2), "取出", 16, GRAY_L)

    text(d, (40, 358), "底库照来源：user_faces 表 → 本地目录", 17, GRAY_L)
    text(d, (40, 390), "比对的是「这张脸是不是该账号的主人」", 17, GRAY_L)

    img.resize((W, H), Image.LANCZOS).save(os.path.join(OUT, "02-怎么做.png"))
    return "02-怎么做.png"


# ---------------- 图3：安全与实测 ----------------
def fig3():
    W, H = 1200, 530
    img, d = canvas(W, H)
    w, h = 520, 150
    xs, ys = [60, 620], [70, 255]
    items = [
        ("只许实时拍摄", "禁止上传相册照片"),
        ("云端静默活体", "挡翻拍、屏幕照"),
        ("多帧一致性校验", "复制同一张图直接拒"),
        ("一次性票据 + 限流", "120 秒 · 60 秒 5 次"),
    ]
    k = 0
    for y in ys:
        for x in xs:
            t, s = items[k]
            rr(d, [x, y, x + w, y + h], 16, fill=BLUE_FILL, outline=BLUE_L)
            badge(d, x + 40, y + 55, k + 1, 18)
            text(d, (x + 76, y + 55), t, 25, INK, bold=True, anchor="lm")
            text(d, (x + 76, y + 96), s, 18, GRAY, anchor="lm")
            k += 1

    by = 445
    rr(d, [60, by, 1140, by + 62], 14, fill=CREAM, outline=(214, 203, 185, 255))
    text(d, (600, by + 31), "人脸照片存服务器本地目录，不对外开放", 19, GRAY, anchor="mm")

    img.resize((W, H), Image.LANCZOS).save(os.path.join(OUT, "03-安全与实测.png"))
    return "03-安全与实测.png"


for f in (fig1, fig2, fig3):
    p = f()
    print("saved", p, os.path.getsize(os.path.join(OUT, p)) // 1024, "KB")
