# -*- coding: utf-8 -*-
"""生成系统架构图 SVG（坐标写死，方便微调；输出 docs/architecture/system-architecture.svg）"""
from pathlib import Path

W, H = 1680, 1322
INK = "#1B1F24"
SUB = "#5B6672"
FAINT = "#8B959F"
BORDER = "#D3DAE1"
BAND = "#F7F9FA"
BAND_LINE = "#E4E9EE"
ACCENT = "#2D6A4F"
ACCENT_BG = "#F2F8F4"

FONT = "'Inter','Source Han Sans SC','Microsoft YaHei','PingFang SC',sans-serif"

out = []
add = out.append


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=12, fill=INK, weight=400, spacing=0, anchor="start", opacity=1.0):
    add(
        f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" fill="{fill}" '
        f'font-weight="{weight}" text-anchor="{anchor}" letter-spacing="{spacing}" '
        f'opacity="{opacity}">{esc(s)}</text>'
    )


def band(x, y, w, h, label, note=""):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{BAND}" stroke="{BAND_LINE}"/>')
    text(x + 16, y + 23, label, 11.5, "#6E7985", 600, 1.4)
    if note:
        text(x + 290, y + 23, note, 10.2, FAINT)


def box(x, y, w, h, title, lines, accent=False, line_fill=SUB, title_size=13.5, size=11.6, lead=17):
    stroke = ACCENT if accent else BORDER
    sw = 2 if accent else 1
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="#FFFFFF" '
        f'stroke="{stroke}" stroke-width="{sw}"/>')
    text(x + 16, y + 25, title, title_size, INK, 600)
    yy = y + 47
    for ln in lines:
        text(x + 16, yy, ln, size, line_fill)
        yy += lead


def arrow_v(x, y1, y2, dashed=False, label=""):
    dash = ' stroke-dasharray="4 4"' if dashed else ""
    add(f'<path d="M {x} {y1} L {x} {y2 - 6}" stroke="{FAINT}" stroke-width="1.3"{dash} fill="none" '
        f'marker-end="url(#ah)"/>')
    if label:
        text(x + 7, (y1 + y2) / 2 + 3, label, 9.5, FAINT)


def arrow_h(x1, x2, y, dashed=False):
    dash = ' stroke-dasharray="4 4"' if dashed else ""
    add(f'<path d="M {x1} {y} L {x2 - 6} {y}" stroke="{FAINT}" stroke-width="1.3"{dash} fill="none" '
        f'marker-end="url(#ah)"/>')


# ---------------- 画布 ----------------
add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
add('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" '
    f'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="{FAINT}"/></marker></defs>')
add(f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>')

# 标题
text(48, 54, "识膳智行 · 系统架构图", 25, INK, 600)
text(48, 82, "依据当前主分支代码绘制（0498f77）：自上而下为客户端、接口层、领域服务、支撑能力与数据；"
             "框内小写英文即源码模块名。", 11.8, SUB)
text(48, 102, "FastAPI + SQLAlchemy + SQLite ｜ Vue 3 + TypeScript ｜ OR-Tools CP-SAT ｜ 17 个路由模块 / 21 张表",
     11.8, FAINT)

# 图例
lx = 1148
arrow_h(lx, lx + 34, 47)
text(lx + 42, 51, "同步调用", 11, SUB)
arrow_h(lx, lx + 34, 69, dashed=True)
text(lx + 42, 73, "可选 / 降级路径", 11, SUB)
add(f'<rect x="{lx}" y="{84}" width="26" height="12" rx="3" fill="{ACCENT_BG}" stroke="{ACCENT}" stroke-width="2"/>')
text(lx + 42, 94, "当前迭代重点", 11, SUB)

# ---------------- 1 客户端 ----------------
band(48, 132, 1584, 148, "客户端  CLIENT", "Vue 3.5 · TypeScript · Pinia · Vue Router · Element Plus · Vite")
box(64, 158, 506, 104, "移动端 · 五大模块", [
    "/m/plan 计划 · /m/schedule 日程 · /m/sport 运动",
    "/m/community 社区 · /m/me 我的",
    "手机浏览器访问，底部 TabBar 切换",
])
box(586, 158, 506, 104, "桌面端 · 完整业务视图", [
    "仪表盘 · 健康档案 · 食物识别 · 养生方案",
    "健康打卡 · 数据追踪 · 运动 · 积分商城",
    "成就徽章 · 排行榜",
])
box(1108, 158, 506, 104, "端侧能力", [
    "MediaPipe 人脸采集 · Leaflet 运动轨迹",
    "ECharts 趋势图 · Web Bluetooth 手环读取",
    "axios 统一带 token / 401 跳登录",
])

# ---------------- 2 接口层 ----------------
band(48, 304, 1584, 148, "接口层  API", "FastAPI · Pydantic v2 · SQLAlchemy 2 · JWT")
box(64, 330, 984, 92, "业务路由  /api/*（17 个模块）", [
    "auth · profile · recognitions · food_records · foods · plans · daily · checkins",
    "wellness · workouts · devices · face · stats · points · rewards · achievements · coach",
    "GET /api/day/summary · POST /api/day/adapt/revert · POST /api/plans/generate",
], size=11.6, lead=17)
box(1064, 330, 552, 92, "横切治理", [
    "JWT Bearer（core/security · deps）· CORS 白名单",
    "写入限流 · /uploads 静态目录",
    "lifespan：建表 / 种子数据 / 手环采样循环",
], size=11.4, lead=18)

arrow_v(317, 280, 304, label="HTTPS / JSON")
arrow_v(1361, 280, 304)

# ---------------- 3 领域服务 ----------------
band(48, 476, 1584, 344, "领域服务  DOMAIN SERVICES", "全部在 backend/app/services/ 下，进程内函数调用")
box(64, 502, 506, 168, "方案生成 · 约束求解", [
    "plan_intent 意图抽取 → plan_targets 目标与处方",
    "planner：OR-Tools CP-SAT 逐日求解",
    "nutrition_check 复核 → plan_lifecycle 激活/顺延",
    "plan_review 周期复盘；PLAN_ENGINE 可切回 LLM 生成",
    "目标口径：BMR/TDEE、蛋白上下限、钠糖上限、运动处方",
])
box(586, 502, 506, 168, "摄入识别 · 视觉与文本", [
    "photo_intake 拍照记餐 · recognition 候选评估",
    "food_text / food_synonyms 文本归一与同义词",
    "food_layers 食材层级 · 份量偏差判定",
    "识别结果 → food_records，供当日口径使用",
    "候选带置信度，用户可改份量或换菜后再落库",
])
box(1108, 502, 506, 168, "当日闭环 · 判定与调整", [
    "settlement 结算时点 → metric_bands 区间与容忍",
    "daily_audit 九项体检 → disposition 偏离处置",
    "adapt：diagnose / preview / apply（可撤销）",
    "recalc · meal_recalc 目标重算与换菜",
    "产出九项状态与后续动作，写回 plan_days.targets",
], accent=True, line_fill="#3E4A56")

box(64, 686, 506, 118, "设备与身份", [
    "device_import 导出包导入 · band_live 分钟级采样",
    "met · workout_calc 运动消耗测算",
    "face 人脸底库比对（face/provider）",
], lead=18)
box(586, 686, 506, 118, "激励与成长", [
    "checkins 打卡 · points 积分账本 · rewards 兑换",
    "achievements 成就解锁 · redis_leaderboard 榜单",
    "积分来自打卡与完成度，兑换记录写 redemptions",
], lead=18)
box(1108, 686, 506, 118, "教练与报告", [
    "coach 对话（LLM）· plan_chat 方案对话",
    "weekly_report 周报（落库缓存）· stats 聚合统计",
    "周报按周缓存，重复打开直接回读不重复生成",
], lead=18)

arrow_v(317, 452, 476)
arrow_v(839, 452, 476, label="进程内调用")
arrow_v(1361, 452, 476)

# ---------------- 4 支撑能力 ----------------
band(48, 844, 1584, 108, "支撑能力  PLATFORM", "可替换适配器；外部依赖失败时降级而不是中断")
box(64, 870, 376, 66, "大模型适配", [
    "provider 路由 · openai_compat",
    "glm / qwen / Ollama · 失败降级 mock",
], title_size=12.5, size=10.8, lead=15)
box(456, 870, 376, 66, "营养数据", [
    "food_seed 种子 · app/data/*.json",
    "食物/菜谱/别名/关系 · canonical 归一",
], title_size=12.5, size=10.8, lead=15)
box(848, 870, 376, 66, "人脸与图像", [
    "face/provider 底库比对",
    "images 落盘 → /uploads 静态访问",
], title_size=12.5, size=10.8, lead=15)
box(1240, 870, 376, 66, "缓存与降级", [
    "Redis ZSet 排行榜（可选）",
    "未启用时回落 SQLite 排序",
], title_size=12.5, size=10.8, lead=15)

arrow_v(317, 820, 844, label="适配器")
arrow_v(1100, 820, 844)

# ---------------- 5 数据与外部 ----------------
band(48, 976, 1584, 150, "数据与外部  DATA / EXTERNAL", "SQLite 单文件随项目走 · 大模型由 .env 切换")
box(64, 1002, 984, 108, "关系库 · SQLite（backend/foodai.db，21 张表）", [
    "users · health_profiles · diet_plans · plan_days · food_records · recognitions · check_ins",
    "water_logs · sleep_logs · weight_logs · diet_deviations · plan_constraint_state · plan_chat",
    "workouts · devices · user_face · points_log · rewards · redemptions · achievements · weekly_report",
], size=11.6, lead=20)
box(1064, 1002, 264, 108, "文件与静态", [
    "uploads/ 图片",
    "app/data/*.json",
    "手环导出包目录",
], size=11, lead=18)
box(1344, 1002, 272, 108, "外部服务", [
    "智谱 GLM-4 / 4v",
    "DashScope Qwen",
    "本地 Ollama",
], size=11, lead=18)

arrow_v(700, 952, 976, label="SQLAlchemy")

# ---------------- 6 关键链路 ----------------
band(48, 1150, 1584, 120, "关键链路  KEY FLOWS", "箭头方向即调用方向")
flows = [
    ("① 记一餐", ["拍照 / 文字 → 视觉识别", "→ 营养库匹配 → food_records", "→ 当日区间重算"]),
    ("② 出方案", ["需求对话 → 意图 / 目标", "→ CP-SAT 逐日求解", "→ 7 天 plan_days，可逐日换菜"]),
    ("③ 当天收口", ["结算时点 → 九项体检", "→ 偏离处置 → 明后天目标改写", "→ 预览 / 一键撤销"]),
    ("④ 手环", ["实时采样 / 导出包", "→ 步数与睡眠指标", "→ 区间判定 · 运动消耗"]),
]
fx = [64, 456, 848, 1240]
for (title, lines), x in zip(flows, fx):
    add(f'<rect x="{x}" y="1178" width="376" height="76" rx="6" fill="#FFFFFF" stroke="{BORDER}"/>')
    text(x + 14, 1198, title, 12.2, INK, 600)
    yy = 1215
    for ln in lines:
        text(x + 14, yy, ln, 10.6, SUB)
        yy += 14

text(48, 1300, "运行时：uvicorn 单进程（含 band_live 后台采样线程）· 前端 Vite 构建为静态资源 · "
              "SQLite 单文件 · Redis 可选（缺失即降级）", 10.5, FAINT)

add("</svg>")

path = Path(__file__).resolve().parent / "system-architecture.svg"
path.write_text("\n".join(out), encoding="utf-8")
print("saved", path)
