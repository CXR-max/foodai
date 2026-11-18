"""生成营养库结构图与数据审查流程图（PNG）。

用法：
    backend\\.venv\\Scripts\\python.exe scripts\\make_nutrition_figures.py

输出：
    docs/figures/nutrition-library-architecture.png
    docs/figures/data-review-pipeline.png
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# matplotlib 默认缓存目录在受限环境下不可写，先指到临时目录
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "mpl-foodai"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "figures"

plt.rcParams["font.family"] = ["Microsoft YaHei"]
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 150

INK = "#1F2937"
MUTED = "#4B5563"
PALETTE = {
    "source": ("#E8F0FE", "#3F6FC4"),
    "staging": ("#FFF3E0", "#C87A1E"),
    "active": ("#E6F4EA", "#2E7D4F"),
    "relation": ("#F3E8FD", "#7343A8"),
    "match": ("#E3F1F8", "#26708F"),
    "app": ("#FCE8E8", "#A93A3A"),
    "gate": ("#F4F4F5", "#6B7280"),
}


def box(ax, x, y, w, h, title, sub="", kind="source", tfs=11.5, sfs=9.2, pad=0.35,
        title_frac=0.72, sub_frac=0.30):
    fc, ec = PALETTE[kind]
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h,
            boxstyle=f"round,pad={pad},rounding_size=1.4",
            linewidth=1.6, facecolor=fc, edgecolor=ec, zorder=2,
        )
    )
    if sub:
        ax.text(x + w / 2, y + h * title_frac, title, ha="center", va="center",
                fontsize=tfs, fontweight="bold", color=INK, zorder=3)
        ax.text(x + w / 2, y + h * sub_frac, sub, ha="center", va="center",
                fontsize=sfs, color=MUTED, linespacing=1.55, zorder=3)
    else:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center",
                fontsize=tfs, fontweight="bold", color=INK, linespacing=1.6, zorder=3)


def arrow(ax, p1, p2, color="#9CA3AF", lw=1.8, style="-|>", ls="-", rad=0.0):
    ax.add_patch(
        FancyArrowPatch(
            p1, p2, arrowstyle=style, mutation_scale=16, linewidth=lw,
            color=color, linestyle=ls, zorder=1,
            connectionstyle=f"arc3,rad={rad}",
        )
    )


def band(ax, y, h, label, x=3, w=154, color="#F9FAFB", edge="#E5E7EB"):
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0.2,rounding_size=1.6",
            linewidth=1.0, facecolor=color, edgecolor=edge, zorder=0,
        )
    )
    ax.text(x + 1.6, y + h - 1.2, label, ha="left", va="top",
            fontsize=10, color="#6B7280", fontweight="bold", zorder=1)


# ---------------------------------------------------------------- 图一
def figure_architecture() -> Path:
    fig, ax = plt.subplots(figsize=(16, 12))
    ax.set_xlim(0, 160)
    ax.set_ylim(0, 120)
    ax.axis("off")

    ax.text(80, 116.4, "营养库分层结构与关系", ha="center", va="center",
            fontsize=21, fontweight="bold", color=INK)
    ax.text(80, 112.8,
            "六层结构：来源可追溯、口径统一到「每 100g」、匹配必留痕",
            ha="center", va="center", fontsize=11.5, color=MUTED)

    # ---- 第 1 层：数据来源
    band(ax, 94.5, 15.2, "① 数据来源层")
    src = [
        "TFDA\n台湾 · 2,213 条",
        "CoFID 2021\n英国 · 2,853 条",
        "USDA FNDDS\n2021-23 · 5,430 条",
        "MEXT 八订\n日本 · 2,538 条",
        "本地种子\n188 条",
        "菜谱 JSON\n201 道",
    ]
    bw, gap = 24.0, 1.5
    x0 = 6.5
    for i, t in enumerate(src):
        box(ax, x0 + i * (bw + gap), 96.4, bw, 8.8, t, kind="source", tfs=10.0)

    # ---- 第 2 层：规范化入库
    band(ax, 77.0, 15.2, "② 规范化入库层")
    box(ax, 6.5, 79.2, 108, 9.4,
        "foods 表（统一 37 列）· staging 未激活 11,576 条",
        "名称/别名/类型/菜系 · 每 100g 七大营养素（热量·蛋白·脂肪·碳水·纤维·钠·糖）· 标准份克重",
        kind="staging", tfs=12.2)
    box(ax, 118.5, 79.2, 35, 9.4, "import_batches\n20 个导入批次",
        "来源 · 版本 · 许可 · 行数 · 校验和", kind="gate", tfs=10.8, sfs=8.6)

    # ---- 第 3 层：活跃池
    band(ax, 58.0, 17.0, "③ 活跃池（active=1，识别可命中的闭集）")
    ax.text(80, 72.2, "1,847 条", ha="center", va="center",
            fontsize=15, fontweight="bold", color="#2E7D4F")
    types = [("食材", "1,317"), ("菜品", "240"), ("单一食物", "215"), ("饮品", "69"), ("轻食", "6")]
    tw, tgap = 27.0, 3.0
    tx0 = 7.0
    for i, (n, c) in enumerate(types):
        box(ax, tx0 + i * (tw + tgap), 61.0, tw, 8.2, f"{n}  {c} 条", kind="active", tfs=11.4)
    ax.text(7.2, 59.6,
            "来源构成：TFDA 1,498 · 菜谱 201 · 本地种子 109 · legacy 39　｜　全部具备标准份克重",
            ha="left", va="center", fontsize=9.6, color=MUTED)

    # ---- 第 4 层：关系与配方
    band(ax, 38.5, 17.5, "④ 关系与配方层")
    box(ax, 6.5, 41.0, 47, 11.0, "别名层  5,276 条",
        "口语名/地方名 → 标准名\nTFDA 5,262 + 种子 14", kind="relation",
        tfs=12.0, sfs=8.8, title_frac=0.74, sub_frac=0.32)
    box(ax, 56.5, 41.0, 47, 11.0, "菜谱层  201 道",
        "1,014 个配方项：食材克数 + 成品克数\n反算每 100g，把整道菜拆成配比", kind="relation",
        tfs=12.0, sfs=8.6, title_frac=0.74, sub_frac=0.32)
    box(ax, 106.5, 41.0, 47, 11.0, "关系层  58 条 / 34 组",
        "同义 31 条（23 组）\n易混淆 27 条（11 组）附区别提示", kind="relation",
        tfs=12.0, sfs=8.8, title_frac=0.74, sub_frac=0.32)

    # ---- 第 5 层：匹配
    band(ax, 21.0, 15.5, "⑤ 识别匹配层（按优先级逐级下探）")
    steps = ["① 精确匹配", "② 同义关系", "③ 别名", "④ 包含（≥3 字）", "⑤ 模糊（≥0.72）", "⑥ 未命中（类别均值）"]
    sw, sgap = 22.0, 3.2
    sx0 = 6.5
    for i, s in enumerate(steps):
        box(ax, sx0 + i * (sw + sgap), 26.2, sw, 6.4, s, kind="match", tfs=10.6)
        if i < len(steps) - 1:
            arrow(ax, (sx0 + i * (sw + sgap) + sw + 0.4, 29.4),
                  (sx0 + (i + 1) * (sw + sgap) - 0.4, 29.4), color="#26708F", lw=1.5)
    ax.text(80, 23.0,
            "置信度 <0.6 / 包含命中 / 模糊命中 / 易混淆组 / 未命中 → 一律要求人工确认，并返回 Top-5 候选",
            ha="center", va="center", fontsize=9.6, color="#A93A3A")

    # ---- 第 6 层：计算与应用
    band(ax, 4.2, 13.6, "⑥ 营养计算与应用层")
    box(ax, 6.5, 5.4, 72, 8.4, "菜谱口径：每 100g = Σ(食材 g ÷ 100 × 食材每100g) ÷ 成品 g × 100",
        kind="app", tfs=10.8)
    box(ax, 81.5, 5.4, 72, 8.4, "识别结果 dishes[] → 每道菜营养与匹配 → food_records → 方案 / 统计 / 积分",
        kind="app", tfs=10.8)

    # 层间箭头
    for y1, y2 in [(96.4, 88.8), (79.0, 75.2), (60.9, 56.2), (40.8, 36.7)]:
        arrow(ax, (80, y1), (80, y2), color="#9CA3AF", lw=2.0)

    ax.text(80, 2.4,
            "设计原则：来源可追溯 · 口径统一（一律每 100g）· 匹配留痕（match_method + match_score）· 不确定必须确认",
            ha="center", va="center", fontsize=10, color=MUTED, style="italic")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "nutrition-library-architecture.png"
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


# ---------------------------------------------------------------- 图二
def figure_pipeline() -> Path:
    fig, ax = plt.subplots(figsize=(15, 13.8))
    ax.set_xlim(0, 150)
    ax.set_ylim(0, 138)
    ax.axis("off")

    ax.text(75, 134.4, "13,423 条营养数据的审查流程", ha="center", va="center",
            fontsize=21, fontweight="bold", color=INK)
    ax.text(75, 130.4, "六道工序串联 · 每步默认 dry-run、写库需显式 --commit、全程留 JSON 报告",
            ha="center", va="center", fontsize=11.2, color=MUTED)

    # (标题, 配色, 说明行, 产出, 关口)
    cards = [
        ("① 原始文件与来源留痕", "source",
         ["输入：TFDA 2,213 · CoFID 2,853 · USDA FNDDS 5,430 · MEXT 2,538",
          "动作：登记批次（来源 / 版本 / 许可 / 行数 / 校验和）",
          "原始 Excel 放 data_sources/，只入库结构化结果，不入库原始大文件"],
         "产出：import_batches 20 条",
         "来源可追到批次\n许可能逐条核对"),
        ("② 规范化入库　canonical.py", "staging",
         ["动作：字段映射到 foods 统一 37 列（七大营养素 + 标准份 + 菜系/类型）",
          "名称归一 name_norm；类型与菜系按规则推断；英文/日文源不参与中文匹配",
          "统一带 verified=0、active=0 进 staging，缺标准份的单独标记"],
         "产出：staging 11,576 条",
         "字段口径统一\n一律折算每 100g"),
        ("③ 质量体检　audit.py", "match",
         ["规则：缺热量 / 热量>900 / 宏量不符（±35%）/ 宏量异常 / 缺标准份 / 重名",
          "附带产出：能在 staging 里找到同名 TFDA 数据的「可替换」候选清单",
          "结果按热量差从大到小排序，便于优先处理偏差最大的条目"],
         "产出：import_reports/quality_report.json",
         "异常逐条列明\n不静默丢数据"),
        ("④ 清洗与替换　refresh.py", "relation",
         ["去重：同一 name_norm 只保留一条，优先级 recipe > app_seed > legacy_json",
          "替换：与 TFDA 官方值热量差 >20% 才覆盖，旧值写入 extra_nutrients.previous",
          "已执行过一轮清洗；当前复跑结果为去重 0 组、替换 0 条，说明活跃池已干净"],
         "产出：去重 / 替换清单（可回溯）",
         "不直接改原值\n旧值另存可回退"),
        ("⑤ 分批提升　promote.py", "active",
         ["过滤：仅中文源；排除取样批次 / 有机 / 平均值 / 年份后缀等脏名",
          "必须有热量、标准份克重，且宏量与热量自洽；与活跃池重名的跳过",
          "排序：蔬菜 / 肉 / 蛋 / 主食等常用词优先；分批推进 --limit 300 --commit"],
         "产出：活跃池 1,847 条",
         "小批量推进\n每批 300 条可复核"),
        ("⑥ 激活与复核　activate.py", "app",
         ["启停：按来源 / 类型 / 菜系 / 精确名 / id，--deactivate 可随时回滚",
          "复核：前端抽检 + 识别回归测试（42 项全绿）；verified 与 active 分开标记",
          "用途：识别时只在活跃池内匹配，未激活的 11,576 条不进提示词、不参与命中"],
         "产出：verified 1,508 条",
         "可随时回滚\n测试通过才放量"),
    ]

    cx, cw = 5.0, 96.0
    ch, gap = 17.6, 2.2
    top = 125.6

    for i, (title, kind, lines, out, gate) in enumerate(cards):
        y = top - (i + 1) * ch - i * gap
        fc, ec = PALETTE[kind]
        ax.add_patch(FancyBboxPatch((cx, y), cw, ch,
                                    boxstyle="round,pad=0.3,rounding_size=1.3",
                                    linewidth=1.7, facecolor=fc, edgecolor=ec, zorder=2))
        ax.text(cx + 2.4, y + ch - 3.0, title, ha="left", va="center",
                fontsize=11.8, fontweight="bold", color=INK, zorder=3)
        ax.plot([cx + 2.4, cx + cw - 2.4], [y + ch - 5.4, y + ch - 5.4],
                color=ec, linewidth=0.9, alpha=0.55, zorder=3)
        for j, line in enumerate(lines):
            ax.text(cx + 2.4, y + ch - 8.2 - j * 2.9, line, ha="left", va="center",
                    fontsize=8.5, color=MUTED, zorder=3)
        ax.text(cx + 2.4, y + 2.0, out, ha="left", va="center",
                fontsize=9.2, color=ec, fontweight="bold", zorder=3)

        # 右侧关口
        gx, gw = 106.0, 39.0
        ax.add_patch(FancyBboxPatch((gx, y + 2.4), gw, ch - 4.8,
                                    boxstyle="round,pad=0.28,rounding_size=1.1",
                                    linewidth=1.2, facecolor="#F4F4F5",
                                    edgecolor="#9CA3AF", zorder=2))
        ax.text(gx + gw / 2, y + ch / 2 + 3.0, "关口", ha="center", va="center",
                fontsize=8.2, color="#6B7280", zorder=3)
        ax.text(gx + gw / 2, y + ch / 2 - 2.2, gate, ha="center", va="center",
                fontsize=8.8, color=INK, linespacing=1.7, zorder=3)
        arrow(ax, (cx + cw + 0.6, y + ch / 2), (gx - 0.6, y + ch / 2),
              color="#B0B7C3", lw=1.3, ls=(0, (3, 2)))

        # 工序之间的下行箭头
        if i < len(cards) - 1:
            arrow(ax, (cx + cw / 2, y - 0.5), (cx + cw / 2, y - gap - 0.3),
                  color="#6B7280", lw=2.2)

    # 底部进度条
    by = 2.6
    ax.add_patch(FancyBboxPatch((5.0, by), 140, 6.2,
                                boxstyle="round,pad=0.3,rounding_size=1.2",
                                linewidth=1.2, facecolor="#E6F4EA",
                                edgecolor="#2E7D4F", zorder=2))
    ax.text(75, by + 3.1,
            "当前进度：13,423 条全部入库 → staging 11,576 条待分批审查 → 活跃池 1,847 条 → verified 1,508 条",
            ha="center", va="center", fontsize=9.6, color="#2E7D4F", fontweight="bold", zorder=3)

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "data-review-pipeline.png"
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


if __name__ == "__main__":
    for p in (figure_architecture(), figure_pipeline()):
        print(p, p.stat().st_size)
