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
        if key == "calorie":
            return f"比下限少 {_fmt(round(gap))} {unit}，长期偏低容易掉肌肉，补一份蛋白+主食"
        if key == "protein":
            return f"还差 {_fmt(round(gap))} {unit} 蛋白，加一个鸡蛋/一杯牛奶/一份鸡胸"
        if key == "water":
            return f"还差 {_fmt(round(gap))} {unit}，分 2~3 次补上，别一次灌"
        if key == "sleep":
            return f"比下限少 {_fmt(round(gap, 1) if unit == 'h' else round(gap))} {unit}，今晚提前 {int(round(gap * 60)) if unit == 'h' else int(gap)} 分钟睡"
        if key == "exercise":
            return f"还差 {_fmt(round(gap))} {unit}，加 {max(5, int(round(gap / 8)))} 分钟快走就够"
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
