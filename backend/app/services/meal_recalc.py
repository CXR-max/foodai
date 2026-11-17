"""改菜后的营养重算与超限处理（不调模型，纯本地口径）

用户改一道菜，牵动的是一整天的数：
- 这道菜的热量 / 蛋白 / 脂肪 / 碳水（按营养库，未收录按同类均值估）
- 这一餐的热量
- 当日总热量、三大营养素 → 和目标区间比对
- 超出上限时给两条出路：按比例缩份量 / 折算成运动时长
"""
from __future__ import annotations

import logging

from app.services import nutrition

logger = logging.getLogger("meal_recalc")

MEAL_ORDER = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}
MEAL_LABELS = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"}

CALORIE_TOLERANCE = 0.10   # 超上限 10% 才提示，避免 20 kcal 也弹警告
SHORT_TOLERANCE = 0.15     # 低于下限 15% 才提示


def item_nutrition(item: dict) -> dict:
    """单道菜 → 营养（命中库精确，未命中按类别均值估）"""
    name = (item.get("name") or "").strip()
    try:
        portion = float(item.get("portion_g") or 0)
    except (TypeError, ValueError):
        portion = 0.0
    if portion <= 0:
        portion = 100.0

    calc = nutrition.match_and_calc(name, portion, item.get("category"))
    return {
        "name": name,
        "portion_g": portion,
        "calories": round(calc["calories"], 1),
        "protein_g": round(calc["protein_g"], 1),
        "fat_g": round(calc["fat_g"], 1),
        "carb_g": round(calc["carb_g"], 1),
        "matched": bool(calc["matched"]),
        "matched_name": calc.get("matched_name"),
        "nutrition_source": calc.get("nutrition_source"),
        "note": calc.get("note"),
    }


def recalc_meals(meals: list[dict]) -> dict:
    """重算一天的餐食；返回 {meals, total, coverage}。

    返回的 meals 里每道菜都带上自己的营养，前端可以直接显示"这道菜多少卡"，
    不用再自己算一遍（两端口径不一致是这类页面最常见的坑）。
    """
    result: list[dict] = []
    matched_items = total_items = 0
    total = {"calories": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carb_g": 0.0}

    for meal in sorted(meals or [], key=lambda m: MEAL_ORDER.get(m.get("meal_type", "snack"), 9)):
        items = [item_nutrition(i) for i in (meal.get("items") or []) if (i.get("name") or "").strip()]
        for item in items:
            total_items += 1
            matched_items += 1 if item["matched"] else 0
            for key in total:
                total[key] += item.get(key) or 0

        result.append({
            **meal,
            "items": items,
            "calories": round(sum(i["calories"] for i in items)),
            "meal_type": meal.get("meal_type", "snack"),
            "name": meal.get("name") or MEAL_LABELS.get(meal.get("meal_type", ""), ""),
        })

    return {
        "meals": result,
        "total": {key: round(value, 1) for key, value in total.items()},
        "coverage": {"matched_items": matched_items, "total_items": total_items},
    }


def evaluate(total: dict, targets: dict) -> dict:
    """当日实算 vs 目标区间 → 状态 + 人话建议"""
    calories = float(total.get("calories") or 0)
    cal_min = float(targets.get("calorie_min") or 0)
    cal_max = float(targets.get("calorie_max") or 0)
    protein_min = float(targets.get("protein_min_g") or 0)
    protein = float(total.get("protein_g") or 0)

    status = "ok"
    warnings: list[dict] = []
    excess = short = 0.0

    if cal_max and calories > cal_max * (1 + CALORIE_TOLERANCE):
        status = "high"
        excess = round(calories - cal_max, 1)
        warnings.append({
            "code": "calorie_high", "level": "warn",
            "message": f"当日 {round(calories)} kcal，超出上限 {round(cal_max)} kcal 约 {round(excess)} kcal",
            "actions": ["portion", "exercise"],
        })
    elif cal_min and calories < cal_min * (1 - SHORT_TOLERANCE):
        status = "low"
        short = round(cal_min - calories, 1)
        warnings.append({
            "code": "calorie_low", "level": "warn",
            "message": f"当日 {round(calories)} kcal，比下限 {round(cal_min)} kcal 少约 {round(short)} kcal",
            "actions": ["add_snack"],
        })

    if protein_min and protein < protein_min * 0.9:
        warnings.append({
            "code": "protein_low", "level": "info",
            "message": f"蛋白 {round(protein)} g，低于目标 {round(protein_min)} g，可把其中一道换成高蛋白的菜",
            "actions": [],
        })

    # 缩份量系数：把当日拉回区间中点
    target_mid = (cal_min + cal_max) / 2 if (cal_min and cal_max) else calories
    factor = round(min(max(target_mid / calories, 0.6), 1.2), 3) if calories else 1.0

    return {
        "status": status,
        "excess_kcal": excess,
        "short_kcal": short,
        "portion_factor": factor,
        "warnings": warnings,
    }


def apply_portion_rebalance(meals: list[dict], factor: float) -> list[dict]:
    """按系数缩放每道菜的克重（5 g 取整，最低 20 g），用于"缩份量回到区间\""""
    if not factor or abs(factor - 1) < 0.02:
        return meals
    scaled: list[dict] = []
    for meal in meals:
        items = []
        for item in (meal.get("items") or []):
            portion = float(item.get("portion_g") or 100)
            new_portion = max(20.0, round(portion * factor / 5.0) * 5.0)
            items.append({**item, "portion_g": new_portion})
        scaled.append({**meal, "items": items})
    return scaled


def summarize(total: dict, targets: dict, status_info: dict) -> dict:
    """给 plan_days.targets 落一份"当日实算"快照，供日程/我的展示已吃 vs 目标"""
    return {
        "calories": round(total.get("calories") or 0),
        "protein_g": round(total.get("protein_g") or 0),
        "fat_g": round(total.get("fat_g") or 0),
        "carb_g": round(total.get("carb_g") or 0),
        "calorie_min": targets.get("calorie_min"),
        "calorie_max": targets.get("calorie_max"),
        "protein_min_g": targets.get("protein_min_g"),
        "status": status_info.get("status"),
        "source": "plan_meals",
    }
