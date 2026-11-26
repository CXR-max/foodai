# -*- coding: utf-8 -*-
"""生成「系统架构图」单页 PPT 专用的 16:9 图（1680×945）

与 build_architecture.py 的区别：这一版是给投屏用的，按 7.pptx 的米色底 + 赭红强调色重排，
信息做减法（每个模块 1~3 行），字号相对放大，整张图铺满 960×540 的页面。
"""
from pathlib import Path

W, H = 1680, 945
BG = "#FAF6F1"        # 与 PPT 页面底色一致，铺满时不露边
INK = "#262220"
SUB = "#6B615A"
FAINT = "#A79C92"
BORDER = "#EADFD2"
BAND = "#F5EEE6"
BAND_LINE = "#EDE2D5"
ACCENT = "#D2694C"
ACCENT_BG = "#FBEDE7"

FONT = "'Microsoft YaHei','Source Han Sans SC','PingFang SC',sans-serif"

out = []
add = out.append


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=13, fill=INK, weight=400, spacing=0, anchor="start"):
    add(
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" fill="{fill}" '
        f'font-weight="{weight}" text-anchor="{anchor}" letter-spacing="{spacing}">{esc(s)}</text>'
    )


def band(x, y, w, h, label, note=""):
    """层色块：顶部 30px 是表头（层名 + 右侧说明），下方留白给方框"""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{BAND}" stroke="{BAND_LINE}"/>')
    text(x + 18, y + 21, label, 12, "#8C837A", 600, 1.2)
    if note:
        text(x + 250, y + 21, note, 11.5, FAINT)


def box(x, y, w, h, title, lines, accent=False, size=13, lead=16.5):
    stroke = ACCENT if accent else BORDER
    sw = 1.8 if accent else 1
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="#FFFFFF" '
        f'stroke="{stroke}" stroke-width="{sw}"/>')
    text(x + 16, y + 24, title, 14.5, INK, 600)
    yy = y + 43
    for ln in lines:
        text(x + 16, yy, ln, size, SUB)
        yy += lead


# ---------------- 画布 ----------------
add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
add(f'<rect width="{W}" height="{H}" fill="{BG}"/>')

# 标题
text(48, 52, "识膳智行 · 系统架构图", 27, INK, 600)
text(48, 78, "五层结构：客户端 → 接口层 → 领域服务 → 支撑能力 → 数据与外部；"
             "框内小写英文为源码模块名", 12.5, SUB)

# 图例（右上，一条）
add(f'<rect x="1526" y="43" width="24" height="12" rx="3" fill="{ACCENT_BG}" stroke="{ACCENT}" stroke-width="1.8"/>')
text(1632, 53, "本周迭代重点", 11.5, SUB, 400, 0, "end")

# ---------------- 1 客户端 ----------------
band(48, 88, 1584, 106, "客户端  CLIENT", "Vue 3.5 · TypeScript · Pinia · Vite")
box(48, 118, 517, 68, "移动端 · 五大模块", [
    "/m/plan · /m/schedule · /m/sport",
    "/m/community · /m/me，底部 TabBar 切换",
])
box(581, 118, 517, 68, "桌面端 · 完整业务视图", [
    "仪表盘 · 健康档案 · 食物识别 · 养生方案",
    "打卡 · 数据追踪 · 运动 · 积分商城 · 成就",
])
box(1114, 118, 518, 68, "端侧能力", [
    "MediaPipe 人脸 · Leaflet 轨迹 · ECharts",
    "Web Bluetooth 手环 · axios 统一鉴权",
])


# ---------------- 2 接口层 ----------------
band(48, 209, 1584, 104, "接口层  API", "FastAPI · Pydantic v2 · JWT｜客户端经 HTTPS / JSON 调用")
box(48, 239, 1024, 68, "业务路由  /api/*（17 个模块）", [
    "auth · profile · recognitions · food_records · foods · plans · daily · checkins",
    "wellness · workouts · devices · face · stats · points · rewards · achievements · coach",
])
box(1088, 239, 544, 68, "横切治理", [
    "JWT Bearer · CORS 白名单 · 写入限流 · /uploads 静态目录",
    "lifespan：建表 / 种子数据 / 手环采样循环",
])


# ---------------- 3 领域服务 ----------------
band(48, 328, 1584, 244, "领域服务  DOMAIN SERVICES", "全部位于 backend/app/services/，由路由进程内调用（非跨服务）")
box(48, 358, 517, 98, "方案生成 · 约束求解", [
    "plan_intent 意图 → plan_targets 目标与处方",
    "planner：OR-Tools CP-SAT 逐日求解",
    "nutrition_check 复核 · plan_lifecycle 顺延",
])
box(581, 358, 517, 98, "摄入识别 · 视觉与文本", [
    "photo_intake 拍照记餐 · recognition 候选评估",
    "food_text / food_synonyms 归一 · food_layers 层级",
    "结果写 food_records，供当日口径使用",
])
box(1114, 358, 518, 98, "当日闭环 · 判定与调整", [
    "settlement 结算 → metric_bands 区间",
    "daily_audit 九项 → disposition 处置",
    "adapt：诊断 / 预告 / 撤销 · recalc 重排",
], accent=True)

box(48, 464, 517, 98, "设备与身份", [
    "device_import 导出包 · band_live 分钟级采样",
    "met · workout_calc 运动消耗测算",
    "face 人脸底库比对（face/provider）",
])
box(581, 464, 517, 98, "激励与成长", [
    "checkins 打卡 · points 积分账本",
    "rewards 兑换 · achievements 成就解锁",
    "redis_leaderboard 榜单（Redis 可选）",
])
box(1114, 464, 518, 98, "教练与报告", [
    "coach 问答 · plan_chat 方案对话（LLM）",
    "weekly_report 周报（落库缓存）",
    "stats 聚合统计（趋势 / 达标率）",
])


# ---------------- 4 支撑能力 ----------------
band(48, 587, 1584, 86, "支撑能力  PLATFORM", "可替换适配器；外部依赖失败时降级而非中断")
box(48, 617, 385, 52, "大模型适配", ["provider 路由 · glm / qwen / Ollama / mock"], size=12)
box(447, 617, 385, 52, "营养数据", ["food_seed 种子 · app/data/*.json"], size=12)
box(846, 617, 385, 52, "人脸与图像", ["face/provider 底库 · images → /uploads"], size=12)
box(1245, 617, 387, 52, "缓存与降级", ["Redis ZSet 可选 · 缺失回落 SQLite"], size=12)


# ---------------- 5 数据与外部 ----------------
band(48, 688, 1584, 104, "数据与外部  DATA / EXTERNAL", "SQLAlchemy 映射 · SQLite 单文件 · 大模型由 .env 切换")
box(48, 718, 1024, 68, "关系库 · SQLite（backend/foodai.db，21 张表）", [
    "users · health_profiles · diet_plans · plan_days · food_records · recognitions · check_ins · water_logs",
    "sleep_logs · diet_deviations · plan_constraint_state · workouts · devices · user_face · points_log · redemptions",
], size=12)
box(1088, 718, 264, 68, "文件与静态", ["uploads/ 图片", "app/data/*.json"], size=12)
box(1368, 718, 264, 68, "外部服务", ["智谱 GLM-4 / 4v", "Qwen · 本地 Ollama"], size=12)

# ---------------- 6 关键链路 ----------------
band(48, 807, 1584, 96, "关键链路  KEY FLOWS", "箭头方向即调用方向")
flows = [
    ("① 记一餐", ["拍照 / 文字 → 视觉识别", "→ 营养库匹配 → 当日重算"]),
    ("② 出方案", ["需求对话 → 意图与目标", "→ CP-SAT 求解 7 天 plan_days"]),
    ("③ 当天收口", ["结算 → 九项体检 → 处置", "→ 明后天目标改写 · 可撤销"]),
    ("④ 手环", ["实时采样 / 导出包", "→ 步数睡眠 → 区间与消耗"]),
]
for i, (title, lines) in enumerate(flows):
    x = 48 + i * 399
    add(f'<rect x="{x}" y="837" width="385" height="58" rx="6" fill="#FFFFFF" stroke="{BORDER}"/>')
    text(x + 14, 858, title, 13, INK, 600)
    yy = 875
    for ln in lines:
        text(x + 14, yy, ln, 11.5, SUB)
        yy += 14

text(48, 930, "运行时：uvicorn 单进程（含 band_live 后台采样线程）· 前端 Vite 构建为静态资源 · "
              "SQLite 单文件 · Redis 可选（缺失即降级）", 11, FAINT)

add("</svg>")

path = Path(__file__).resolve().parent / "architecture-slide.svg"
path.write_text("\n".join(out), encoding="utf-8")
print("saved", path)
