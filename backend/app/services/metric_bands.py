"""每日打卡指标区间：给定目标与实际值，算出「确切区间 + 浮动容忍 + 达标结论」

为什么要区间而不是单一目标：
- 真实生活里 1900 kcal 不可能精准命中，卡死单值会让用户天天「未达标」
- 生理指标本身有波动（体重、睡眠、饮水都受当天活动与天气影响），
  所以每个指标给三段：**推荐区间 [min, max]**、**浮动带 [soft_min, soft_max]**、
  **实际值 actual**，判定分五档，日程/我的/评价/打卡四条链路共用同一口径

判定分档（避免「差一点就全盘失败」）：
- in_range 落在推荐区间 → 满分
- low / high 掉出区间但在浮动带内 → 算达标，给一句「略低/略高」提示（按 75% 计分）
- under / over 掉出浮动带 → 未达标，给补救建议

所有阈值都不写死在前端，改口径只改这里。
"""
from __future__ import annotations

from typing import Iterable

from app.services import nutrition_check

# 各指标的浮动容忍：ratio 表示相对上下限的百分比，abs 表示绝对量（更大者优先）
# round_to 用于把展示数字修整成「人看得懂」的刻度
BAND_SPECS: dict[str, dict] = {
    "calorie": {
        "label": "热量", "unit": "kcal",
        "min_key": "calorie_min", "max_key": "calorie_max", "target_key": "calorie_target",
        "soft_min_ratio": 0.05, "soft_max_ratio": 0.05, "soft_min_abs": 80, "soft_max_abs": 80,
        "round_to": 10,
    },
    "protein": {
        "label": "蛋白", "unit": "g",
        "min_key": "protein_min_g", "max_key": "protein_max_g",
        "soft_min_ratio": 0.15, "soft_max_ratio": 0.30,
        "round_to": 1,
    },
    "water": {
        "label": "饮水", "unit": "ml",
        "min_key": "water_min_ml", "max_key": "water_max_ml",
        "soft_min_ratio": 0.10, "soft_max_ratio": 0.30,
        "round_to": 50,
    },
    "sleep": {
        "label": "睡眠", "unit": "h",
        "min_key": "sleep_min_h", "max_key": "sleep_max_h",
        "soft_min_abs": 0.5, "soft_max_abs": 1.0,
        "round_to": 0.5,
    },
    "exercise": {
        "label": "运动消耗", "unit": "kcal",
        "min_key": "exercise_burn_min", "max_key": "exercise_burn_max",
        "soft_min_ratio": 0.15, "soft_max_ratio": 0.60,
        "round_to": 10,
    },
    "steps": {
        "label": "步数", "unit": "步",
        "min_key": "steps_min", "max_key": "steps_max",
        "soft_min_ratio": 0.15, "soft_max_ratio": 0.50,
        "round_to": 100,
    },
}

STATUS_TEXT = {
    "in_range": "达标",
    "low": "略低",
    "high": "略高",
    "under": "不足",
    "over": "超标",
    "no_data": "待记录",
}

# 评价计分权重（in_range 满分，浮动带内按 75%）
STATUS_FACTOR = {"in_range": 1.0, "low": 0.75, "high": 0.75, "under": 0.0, "over": 0.0}


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _round(value, step: float):
    if value is None:
        return None
    if not step:
        return round(value, 1)
    return round(round(value / step) * step, 2 if step < 1 else 0)


def band_for(targets: dict, key: str) -> dict | None:
    """按目标算单个指标的推荐区间与浮动带；缺上下限就返回 None（该维度不判定）"""
    spec = BAND_SPECS.get(key)
    if not spec or not targets:
        return None
    low = _num(targets.get(spec["min_key"]))
    high = _num(targets.get(spec["max_key"]))
    if low is None and high is None:
        return None
    if low is None:
        low = high
    if high is None:
        high = low
    if high < low:
        low, high = high, low

    step = spec.get("round_to") or 1
    soft_min = low - max(low * spec.get("soft_min_ratio", 0), spec.get("soft_min_abs", 0))
    soft_max = high + max(high * spec.get("soft_max_ratio", 0), spec.get("soft_max_abs", 0))

    return {
        "key": key,
        "label": spec["label"],
        "unit": spec["unit"],
        "min": _round(low, step),
        "max": _round(high, step),
        "target": _round(_num(targets.get(spec["target_key"])), step) if spec.get("target_key") else None,
        "soft_min": _round(max(soft_min, 0), step),
        "soft_max": _round(soft_max, step),
        "range_text": _range_text(_round(low, step), _round(high, step), spec["unit"]),
    }


def _range_text(low: float, high: float, unit: str) -> str:
    if low == high:
        return f"{_fmt(low)} {unit}"
    if unit == "h":
        return f"{_fmt(low)}~{_fmt(high)} h"
    return f"{_fmt(low)}~{_fmt(high)} {unit}"


def _fmt(value: float) -> str:
    return f"{value:g}"


def status_of(actual: float | None, band: dict) -> str:
    if actual is None:
        return "no_data"
    value = float(actual)
    if value < band["soft_min"]:
        return "under"
    if value < band["min"]:
        return "low"
    if value > band["soft_max"]:
        return "over"
    if value > band["max"]:
        return "high"
    return "in_range"


def _hint(key: str, status: str, actual: float | None, band: dict) -> str:
    """一句人话：差多少、怎么补。文案要能直接显示给用户。"""
    if status == "no_data":
        # 这些指标点打卡就会自动落数据（餐食按方案折算、饮水/睡眠按计划落记录），
        # 不该再让用户"记一下"
        if key in ("calorie", "protein", "exercise", "water", "sleep"):
            return "完成打卡后自动判定"
        return "还没有记录，记一下就能判定"
    value = float(actual or 0)
    unit = band["unit"]
    if status == "in_range":
        return f"落在 {band['range_text']} 区间内"
    if status in ("low", "high"):
        return f"落在 {band['range_text']} 的浮动容忍内，算达标" + (
            "，略低于推荐下限" if status == "low" else "，略高于推荐上限"
        )
    if status == "under":
        gap = band["min"] - value
        # 不推荐「再吃点 XX」：菜品已在每日方案里排好，这里只提醒按方案吃完/查漏记
        if key == "calorie":
            return f"比下限少 {_fmt(round(gap))} {unit}，按今天方案的餐量吃完就够"
        if key == "protein":
            return f"还差 {_fmt(round(gap))} {unit}，按方案吃完今天的菜就够；已吃完仍不足多半是漏记"
        if key == "water":
            return f"还差 {_fmt(round(gap))} {unit}，分 2~3 次补上，别一次灌"
        if key == "sleep":
            return f"比下限少 {_fmt(round(gap, 1) if unit == 'h' else round(gap))} {unit}，今晚提前 {int(round(gap * 60)) if unit == 'h' else int(gap)} 分钟睡"
        if key == "exercise":
            return f"还差 {_fmt(round(gap))} {unit}，按方案的运动做完就够"
        return f"比下限少 {_fmt(round(gap))} {unit}"
    # over
    gap = value - band["max"]
    if key == "calorie":
        return f"超上限 {_fmt(round(gap))} {unit}，下一餐减主食或在「编辑这一天」里加运动抵消"
    if key == "protein":
        return f"超上限 {_fmt(round(gap))} {unit}，蛋白吃太多也存不住，下一餐减一点"
    if key == "water":
        return f"超上限 {_fmt(round(gap))} {unit}，短时间大量饮水会稀释电解质，放慢节奏"
    if key == "sleep":
        return f"超上限 {_fmt(round(gap, 1) if unit == 'h' else round(gap))} {unit}，睡太久也会发沉，作息拉回固定点"
    if key == "exercise":
        return f"超上限 {_fmt(round(gap))} {unit}，注意恢复，明天排一个轻量日"
    return f"超上限 {_fmt(round(gap))} {unit}"


def build_band(targets: dict, key: str, actual: float | None) -> dict | None:
    """单个指标 → 带 actual/status/hint 的完整字典"""
    band = band_for(targets, key)
    if band is None:
        return None
    value = _num(actual)
    status = status_of(value, band)
    band.update({
        "actual": _round(value, BAND_SPECS[key].get("round_to") or 1) if value is not None else None,
        "status": status,
        "status_text": STATUS_TEXT[status],
        "hint": _hint(key, status, value, band),
        "factor": STATUS_FACTOR.get(status),
    })
    return band


def build_bands(targets: dict, actuals: dict, keys: Iterable[str] | None = None) -> list[dict]:
    """一次算全部指标；actuals 用指标 key 传（calorie/protein/water/sleep/exercise/steps）"""
    order = list(keys) if keys else ["calorie", "protein", "water", "sleep", "exercise", "steps"]
    out = []
    for key in order:
        band = build_band(targets, key, actuals.get(key))
        if band is not None:
            out.append(band)
    return out


def achieved(targets: dict, key: str, actual: float | None) -> tuple[bool, dict | None]:
    """打卡判定：落在浮动带内即算完成（避免 6.9h vs 7h 卡死）"""
    band = build_band(targets, key, actual)
    if band is None:
        return False, None
    return band["status"] in ("in_range", "low", "high"), band


# ---------------- 饮食合并判定：七项一次判完 ----------------
# 蛋白不单独打卡（每餐方案本身就按体重配好了），点「午餐」时一次判完；
# 评分上热量 60%、蛋白 40%——蛋白少吃一天不重要，连续一周才重要。
DIET_WEIGHTS = {"calorie": 0.60, "protein": 0.40}
DIET_TEXT = {
    "ok": "饮食达标",
    "in_progress": "进行中",
    "protein_low": "蛋白不足",
    "protein_over": "蛋白偏高",
    "calorie_over": "热量超标",
    "calorie_under": "热量不足",
    "plan_over": "方案超标",
    "extra_over": "额外摄入超标",
    "no_data": "待记录",
}

# 归因标签：前端按这个给不同出口，也决定处置方向
ATTRIBUTION_TEXT = {
    "plan": "方案排的",
    "portion": "份量问题",
    "extra": "方案外吃的",
    "user": "当天摄入",
    "intake": "没吃够",
}

# 份量偏离的判定（**日汇总**口径）：方案内实际 − 方案基线 ≥ max(150 kcal, 基线的 10%)
# 为什么日汇总用 10% 而单餐用 20%：这里比的是全天，1900 kcal 的一天多 190 就是 10%，
# 已经够让当天超标；单餐基数只有五六百，20% 才等价于同样的绝对量。
# 照搬同一个比例，会让「一顿多吃半碗饭」在日汇总里被算成正常波动。
PORTION_MIN_KCAL = 150
PORTION_MIN_RATIO = 0.10


def pending_meal_hint(labels: list[str] | None = None) -> str:
    """「还有晚餐、加餐没打卡，吃完再判」——进行中状态的统一说法"""
    names = [x for x in (labels or []) if x]
    if not names:
        return "还有餐没打卡，吃完再判"
    return f"还有{'、'.join(names)}没打卡，吃完再判"


def _over_text(key: str, item: dict, *, blame_plan: bool) -> str:
    """超标文案：归因给方案还是给用户的额外摄入，说法完全不同"""
    label = item["label"]
    value, high = item["value"], item["max"]
    gap = abs(item.get("delta") or 0)
    if blame_plan:
        return (
            f"这天的方案{label}配到 {value:g} {item['unit']}，比区间高 {gap:g} {item['unit']}。"
            "这是方案的问题，不是你的问题，下次生成会收紧。"
        )
    return f"{label}超出区间 {gap:g} {item['unit']}（{value:g}/{item['range_text']}）"


def diet_summary(targets: dict, calories: float | None = None, protein_g: float | None = None,
                 pending_meals: int = 0, pending_labels: list[str] | None = None,
                 intake: dict | None = None, mode_effect: dict | None = None) -> dict | None:
    """饮食达成：七项合成一条结论 + 谁的问题

    归因四类（这是这一层最重要的产出，前端据此给不同出口）：
    - plan_over     方案内就超了 → 方案排错（P0 之后应为 0，出现即进修复队列），不要求用户少吃
    - extra_over    方案内正常、方案外吃多了 → 用户主动选择，给出口但不指责
    - calorie_under 都打卡了仍低 → 先问「吃不下还是没时间」，再改方案内容
    - protein_low   蛋白不足不阻塞打卡，灰字提示（菜已排好，按方案吃完就够）

    mode_effect：当天的例外场景口径（聚餐/生病/出差…），见 services/day_mode.py。
    被放宽的项（relaxed_keys）不参与 over/under 判定，只留提示；
    pause_scoring 时不给评分系数——生病那天不该被扣分。
    """
    mode_effect = mode_effect or {}
    relaxed_keys = set(mode_effect.get("relaxed_keys") or [])
    nutrients = dict(intake or {})
    if calories is not None:
        nutrients.setdefault("calories", calories)
    if protein_g is not None:
        nutrients.setdefault("protein_g", protein_g)

    def _value(key: str) -> float | None:
        return _num(nutrients.get(nutrition_check.DIET_SPECS[key][4]))

    # 一条记录都没有、也没有折算值：这是「还没吃」而不是「吃了 0」，别判成不足
    has_data = bool((intake or {}).get("records")) or (_num(nutrients.get("calories")) or 0) > 0
    if not has_data:
        return {
            "status": "no_data", "status_text": DIET_TEXT["no_data"], "factor": None,
            "ok": False, "line": "", "hint": "完成用餐打卡后，这里自动按方案判七项营养",
            "calorie": None, "protein": None, "items": [], "attribution": None,
            "attribution_text": "", "portion": None,
            "structure": [], "plan": None, "extra": None,
        }

    # 七项里「有数据才判」：老调用只给热量蛋白，其余项不硬判
    items = {}
    for key in nutrition_check.DIET_KEYS:
        value = _value(key)
        if value is None:
            continue
        items[key] = nutrition_check.item_for(key, nutrients, targets)

    if not items:
        return {
            "status": "no_data", "status_text": DIET_TEXT["no_data"], "factor": None,
            "ok": False, "line": "", "hint": "完成用餐打卡后，这里自动按方案判七项营养",
            "calorie": None, "protein": None, "items": [], "attribution": None,
            "attribution_text": "", "portion": None,
        }

    cal = items.get("calorie")
    pro = items.get("protein")

    def _factor(key: str) -> float | None:
        item = items.get(key)
        if item is None:
            return None
        return {"in_range": 1.0, "under": 0.0, "over": 0.0}.get(item["status"])

    scored = [
        (_factor(key), DIET_WEIGHTS[key])
        for key in ("calorie", "protein")
        if _factor(key) is not None
    ]
    weight_sum = sum(w for _f, w in scored) if scored else 0
    factor = round(sum(f * w for f, w in scored) / weight_sum, 3) if weight_sum else None

    # ---- 归因 ----
    plan_part = (intake or {}).get("plan") or {}
    extra_part = (intake or {}).get("extra") or {}
    # 方案基线 = 方案本来给这餐排了多少。有它才能把「方案排多了」和「你盛多了」分开：
    # 前者只能改方案（用户没责任），后者可以让下一餐紧一点（可矫正）。
    # 没有基线（老记录、纯手动记录）时退回方案内实际值，至少不倒退。
    plan_baseline = (intake or {}).get("plan_baseline") or {}
    has_split = bool(plan_part) or bool(extra_part)
    blame_source = plan_baseline or plan_part
    plan_check = nutrition_check.check(blame_source, targets) if has_split and blame_source else None
    plan_over_items = [v for v in (plan_check or {}).get("violations", []) if v["status"] == "over"]

    # 份量偏离：方案内实际 − 基线（正=盛多了）。这一项不参与「方案错没错」的判定，
    # 只决定越界之后该谁去调整。
    base_cal = _num(plan_baseline.get("calories")) if plan_baseline else None
    plan_cal = _num(plan_part.get("calories")) if plan_part else None
    portion_delta = round(plan_cal - base_cal, 1) if (base_cal and plan_cal is not None) else None
    portion_significant = (
        portion_delta is not None and base_cal
        and abs(portion_delta) >= max(PORTION_MIN_KCAL, base_cal * PORTION_MIN_RATIO)
    )
    extra_cal = _num(extra_part.get("calories")) if extra_part else 0.0

    # 例外场景里被放宽的项不算超标：聚餐日的钠、生病日的蛋白，都不是用户能控的
    over_items = [item for item in items.values()
                  if item["status"] == "over" and item["key"] not in relaxed_keys]
    under_items = [item for item in items.values()
                   if item["status"] == "under" and item["key"] not in relaxed_keys]
    relaxed_hits = [item for item in items.values()
                    if item["key"] in relaxed_keys and item["status"] in ("over", "under")]

    status = "ok"
    status_text = ""
    attribution = None
    hints: list[str] = []

    if plan_over_items:
        status, attribution = "plan_over", "plan"
        worst = max(plan_over_items, key=lambda v: abs(v.get("delta") or 0))
        hints.append(_over_text(worst["key"], worst, blame_plan=True))
    elif over_items:
        status = "calorie_over" if (cal and cal["status"] == "over") else "protein_over"
        # 谁该负责：方案排的本来就超 → 上面已拦；这里只剩「份量」和「加餐」两种可能，
        # 按各自贡献的热量定主因，别把两份饭算成一次加餐
        if extra_cal and extra_cal >= max(portion_delta or 0, 0):
            attribution = "extra"
        elif portion_significant and (portion_delta or 0) > 0:
            attribution = "portion"
        elif extra_cal:
            attribution = "extra"
        else:
            attribution = "user"
        worst = over_items[0]
        hints.append(_over_text(worst["key"], worst, blame_plan=False))
        # 蛋白质没超、超的是脂肪/钠/糖时，别把标签挂到蛋白头上（用户会以为系统算错了）
        if status == "protein_over" and not (pro and pro["status"] == "over"):
            status_text = f"{worst['label']}超标"
        if attribution == "portion":
            # 份量问题可以矫正，但不能骂人：说清差在哪、给一个具体动作
            hints.append(
                f"这餐比方案多吃 {int(round(portion_delta or 0))} kcal，是本餐份量偏大，不是方案排错。"
                "把还没吃的那餐主食减半碗，或者散步 20 分钟就能拉平；明天照常吃。"
            )
        elif attribution == "extra":
            # 不指责、不催：给一个默认「什么都不用做」的出口
            hints.append("记下了，明天照常吃就行；想补回来就把今天剩下的餐减一点，或者散步 20 分钟")
    elif under_items:
        status = "calorie_under" if (cal and cal["status"] == "under") else "protein_low"
        if portion_significant and (portion_delta or 0) < 0 and pending_meals == 0:
            attribution = "portion"
        else:
            attribution = "intake" if pending_meals == 0 else None
        worst = under_items[0]
        gap = abs(worst.get("delta") or 0)
        if status == "calorie_under" and pending_meals == 0:
            if attribution == "portion":
                hints.append(
                    f"这餐比方案少 {int(round(abs(portion_delta or 0)))} kcal，"
                    f"全天比下限还少 {gap:g} {worst['unit']}。"
                    "是这份没吃完，还是方案给的量偏大？补一份或者把后面几天的量调上去都行"
                )
            else:
                hints.append(
                    f"比下限少 {gap:g} {worst['unit']}。"
                    "是吃不下，还是没时间做？我按这个把后面的餐改一下（总量不变，只改体积和做法）"
                )
        elif worst["key"] == "protein":
            # 菜已在每日方案里排好，不推荐「再吃点 XX」，只提醒按方案吃完/查漏记
            hints.append(
                f"蛋白比下限少 {gap:g} g，按方案吃完今天的菜就够；已吃完仍不足多半是漏记"
            )
        else:
            hints.append(f"{worst['label']}比下限少 {gap:g} {worst['unit']}")

        # 整体不足、但某餐份量偏大：差额是"别的餐没吃出来"，不是"这餐吃少了"
        # 不说清的话，用户看到「比下限少 40」会以为全天都吃少了，转头再去加餐
        if portion_significant and (portion_delta or 0) > 0:
            hints.append(
                f"其中有一餐比方案多吃 {int(round(portion_delta or 0))} kcal，"
                "总量还是低，是其他餐没吃够——要补就补没吃的那几餐"
            )

    # 还有餐没打卡时，"不足"只是没吃完，不是吃错了——判红会让人以为方案有问题
    if pending_meals > 0 and status in ("calorie_under", "protein_low"):
        status, attribution = "in_progress", None
        hints = [f"已记 {int(round(_value('calorie') or 0))} kcal，{pending_meal_hint(pending_labels)}"]

    # 例外场景：放宽项单独说一句，避免用户看着"超标"以为自己搞砸了
    if relaxed_hits and mode_effect.get("mode") not in (None, "normal"):
        names = "、".join(item["label"] for item in relaxed_hits)
        hints.append(
            f"{mode_effect.get('label')}：{names}不计超标"
            + (f"（{mode_effect.get('note_text')}）" if mode_effect.get("note_text") else "")
        )

    # 生病/生理期不评分：这时候的偏离不是自控问题，扣分只会让人放弃
    if mode_effect.get("pause_scoring"):
        factor = None
        if status not in ("no_data", "in_progress"):
            hints.append(f"今天按「{mode_effect.get('label')}」不评等级，吃下去、喝够水就够了")

    def piece(key: str) -> str:
        item = items.get(key)
        if not item:
            return ""
        value = item["value"]
        if value is None:
            shown = "—"
        elif key in ("calorie", "sodium"):
            shown = f"{round(value)}"      # 热量/钠按整数显示，1202.8 看着像没算完
        else:
            shown = f"{value:g}"
        return f"{item['label']} {shown}/{item['range_text'].split(' ')[0]}"

    line = " · ".join(x for x in (piece("calorie"), piece("protein")) if x)
    if line:
        line = f"{line} · {status_text or DIET_TEXT.get(status, '待记录')}"

    # 结构项直接复用逐项结果（带 unit/min/max），前端不用再拼单位
    structure = [
        items[key] for key in ("fat", "carb", "fiber", "sodium", "sugar") if key in items
    ]

    return {
        "status": status,
        "status_text": status_text or DIET_TEXT.get(status, "待记录"),
        "factor": factor,
        "ok": status == "ok",
        "line": line,
        "hint": "；".join(h for h in hints if h),
        "attribution": attribution,
        "attribution_text": ATTRIBUTION_TEXT.get(attribution or "", ""),
        # 份量偏离单列一块：前端要显示「方案 760 / 你吃了 950，多 190」，
        # 处置框也要据此给出「缩下一餐」而不是「改方案」
        "portion": {
            "delta_kcal": portion_delta,
            "baseline_kcal": round(base_cal) if base_cal else None,
            "plan_kcal": round(plan_cal) if plan_cal is not None else None,
            "significant": bool(portion_significant),
            "direction": ("over" if (portion_delta or 0) > 0
                          else "under" if (portion_delta or 0) < 0 else "none"),
        } if portion_delta is not None else None,
        "items": [items[key] for key in nutrition_check.DIET_KEYS if key in items],
        "structure": structure,
        "calorie": cal,
        "protein": pro,
        "plan": plan_part or None,
        "extra": extra_part or None,
        "mode": {
            "mode": mode_effect.get("mode") or "normal",
            "label": mode_effect.get("label") or "",
            "note": mode_effect.get("note_text") or "",
            "relaxed": sorted(relaxed_keys),
            "pause_scoring": bool(mode_effect.get("pause_scoring")),
        } if mode_effect.get("mode") not in (None, "normal") else None,
    }


def effective_calorie_band(targets: dict) -> dict:
    """热量区间按当日运动量浮动：训练日额外消耗的 40% 补到上限

    依据：训练日多消耗的这部分需要部分回补，否则连续高强度容易掉肌肉；
    下限不动（下限保的是基础代谢）。返回复制后的 targets，不污染原对象。
    """
    merged = dict(targets or {})
    if not merged.get("is_training_day"):
        return merged
    burn = _num(merged.get("exercise_burn_target"))
    if not burn or burn <= 0:
        return merged
    bump = round(burn * 0.4 / 10.0) * 10
    base_max = _num(merged.get("calorie_max"))
    if base_max and bump > 0:
        merged["calorie_max"] = int(base_max + bump)
        merged["calorie_band_note"] = (
            f"今天训练多吃点没关系：训练预计消耗 {int(burn)} kcal，上限已补回 +{int(bump)} kcal"
        )
    return merged
