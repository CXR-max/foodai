"""七项营养判定：生成侧自检与展示侧判定共用这一份

为什么要单独一个模块：
「生成时按一套系数排、展示时按另一套区间判」是这类系统最典型的坑——
上次出现「日程 1640~1910、三环 1640~1990、方案排到蛋白 133 g」就是两套数打架。
这里把七个数值键、区间键、单位只定义一次，两边都读它。

七项（成人，全天口径）：
- 热量、蛋白质、脂肪、碳水：上下限都判（吃完不许超）
- 膳食纤维：只有下限
- 钠、添加糖：只有上限

判定用**严格区间**（不加浮动容忍）：生成侧要的是「按方案吃完一定落在区间内」，
浮动容忍是给人吃的真实记录用的，不是给方案承诺用的。容差只用来吸收四舍五入。
"""
from __future__ import annotations

# 键顺序即展示顺序：先热量蛋白（用户最关心），再结构项
DIET_KEYS: tuple[str, ...] = ("calorie", "protein", "fat", "carb", "fiber", "sodium", "sugar")

# key: (中文名, 单位, 下限键, 上限键, 营养值键)
DIET_SPECS: dict[str, tuple[str, str, str | None, str | None, str]] = {
    "calorie": ("热量", "kcal", "calorie_min", "calorie_max", "calories"),
    "protein": ("蛋白质", "g", "protein_min_g", "protein_max_g", "protein_g"),
    "fat": ("脂肪", "g", "fat_min_g", "fat_max_g", "fat_g"),
    "carb": ("碳水", "g", "carb_min_g", "carb_max_g", "carb_g"),
    "fiber": ("膳食纤维", "g", "fiber_min_g", None, "fiber_g"),
    "sodium": ("钠", "mg", None, "sodium_max_mg", "sodium_mg"),
    "sugar": ("添加糖", "g", None, "sugar_max_g", "sugar_g"),
}

STATUS_TEXT = {
    "in_range": "达标",
    "under": "不足",
    "over": "超标",
    "unknown": "数据缺失",
    "no_data": "未记录",
}

# 只吸收四舍五入，不做「差一点算达标」的宽容（那是给人吃的记录用的）
ROUND_TOLERANCE = {
    "calorie": 5, "protein": 1, "fat": 1, "carb": 2,
    "fiber": 1, "sodium": 20, "sugar": 1,
}

# 结构项：允许降级（菜库数据缺、烹饪加盐未计入），降级必须留痕
STRUCTURAL_KEYS = frozenset({"fiber", "sodium", "sugar"})


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def item_for(key: str, nutrition: dict, targets: dict) -> dict:
    """单项判定：值 → 区间 → 状态"""
    label, unit, min_key, max_key, value_key = DIET_SPECS[key]
    low = _num(targets.get(min_key)) if min_key else None
    high = _num(targets.get(max_key)) if max_key else None
    raw = nutrition.get(value_key)
    value = _num(raw)

    item = {
        "key": key,
        "label": label,
        "unit": unit,
        "value": round(value, 1) if value is not None else None,
        # actual 是旧字段名，前端与评价在用它，别改名
        "actual": round(value, 1) if value is not None else None,
        "min": low,
        "max": high,
        "range_text": _range_text(low, high, unit),
    }
    if value is None:
        item["status"] = "unknown"
        item["status_text"] = STATUS_TEXT["unknown"]
        item["delta"] = None
        return item

    tolerance = ROUND_TOLERANCE.get(key, 1)
    if low is not None and value < low - tolerance:
        status, delta = "under", round(value - low, 1)
    elif high is not None and value > high + tolerance:
        status, delta = "over", round(value - high, 1)
    else:
        status, delta = "in_range", 0.0
    item["status"] = status
    item["status_text"] = STATUS_TEXT[status]
    item["delta"] = delta
    return item


def _range_text(low: float | None, high: float | None, unit: str) -> str:
    fmt = lambda v: f"{v:g}"  # noqa: E731
    if low is not None and high is not None:
        return f"{fmt(low)}~{fmt(high)} {unit}"
    if low is not None:
        return f"≥{fmt(low)} {unit}"
    if high is not None:
        return f"≤{fmt(high)} {unit}"
    return ""


def check(nutrition: dict, targets: dict, *, soft_keys: frozenset[str] | None = None) -> dict:
    """七项全判，返回是否可交付

    soft_keys：本等级允许放宽的项（结构项降级时传入），这些项只提示不算违约，
    并且在 relaxed 里单独列出，前端要看得见「这天纤维放宽了」。
    """
    soft = soft_keys or frozenset()
    items = {key: item_for(key, nutrition or {}, targets or {}) for key in DIET_KEYS}

    violations: list[dict] = []
    relaxed: list[dict] = []
    unverified: list[str] = []

    for key, item in items.items():
        if item["status"] in ("in_range",):
            continue
        if item["status"] == "unknown":
            # 数据缺失：只有结构项可以容忍（菜库没有这项数据，不是用户的错）
            if key in STRUCTURAL_KEYS:
                unverified.append(key)
                continue
            violations.append({**item, "reason": "missing_data"})
            continue
        if key in soft:
            relaxed.append(item)
            continue
        violations.append({**item, "reason": item["status"]})

    return {
        "ok": not violations,
        "items": [items[key] for key in DIET_KEYS],
        "by_key": items,
        "violations": violations,
        "relaxed": relaxed,
        "unverified": unverified,
    }


def summary_line(nutrition: dict, targets: dict) -> str:
    """一行话：「热量 1830/1640~1910 · 蛋白 82/66~88 · 脂肪 54/40~61」"""
    parts = []
    for key in DIET_KEYS:
        item = item_for(key, nutrition or {}, targets or {})
        if item["value"] is None:
            continue
        parts.append(f"{item['label']} {item['value']:g}/{item['range_text']}")
    return " · ".join(parts)


def violation_text(violation: dict) -> str:
    """违约项的人话：谁的问题、差多少"""
    label = violation.get("label") or violation.get("key")
    value = violation.get("value")
    if violation.get("reason") == "missing_data":
        return f"{label} 缺营养数据，无法确认是否落在区间内"
    if violation.get("status") == "over":
        return f"{label} {value:g} {violation['unit']}，超上限 {abs(violation['delta'] or 0):g}"
    return f"{label} {value:g} {violation['unit']}，比下限少 {abs(violation['delta'] or 0):g}"
