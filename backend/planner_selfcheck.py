"""
约束求解方案自测：两个固定画像 × 7 天

用法（在 backend/ 下）：
    ./.venv/Scripts/python.exe planner_selfcheck.py

输出：每天热量偏差、蛋白达标情况、结构约束校验、达标天数。
任何一天不达标（热量 ±7% / 蛋白下限 / 每餐 1~3 道菜 / 同菜不重复 / 午晚有荤菜）脚本以非 0 退出。
"""
import sys
from time import perf_counter

from app.services.llm import foods_data
from app.services.planner import PROTEIN_FACTORS, build_plan

TARGET_KCAL = 1800
DAYS = 7

PROFILES = [
    {
        "label": "男 175cm/75kg 减脂 1800kcal",
        "profile_ctx": {
            "height_cm": 175, "weight_kg": 75, "age": 30, "gender": "male",
            "activity_level": "moderate", "goal": "lose",
            "daily_target_calories": TARGET_KCAL,
            "taste_preferences": [], "dietary_restrictions": [],
            "exercise_preferences": [],
        },
    },
    {
        "label": "女 160cm/55kg 维持 1800kcal",
        "profile_ctx": {
            "height_cm": 160, "weight_kg": 55, "age": 30, "gender": "female",
            "activity_level": "moderate", "goal": "maintain",
            "daily_target_calories": TARGET_KCAL,
            "taste_preferences": [], "dietary_restrictions": [],
            "exercise_preferences": [],
        },
    },
]

FOODS = {food["name"]: food for food in foods_data.load_foods()}
MEAL_ORDER = ("breakfast", "lunch", "dinner", "snack")


def _nutrition(meal) -> tuple[float, float]:
    """按实际克重重算这一餐的热量/蛋白（不读 PlanMeal.calories）"""
    calories = protein = 0.0
    for item in meal.items:
        food = FOODS[item.name]
        nut = foods_data.calc_nutrition(food, item.portion_g)
        calories += nut["calories"]
        protein += nut["protein_g"]
    return calories, protein


def check_day(day, required_protein: float) -> tuple[bool, list[str], float, float]:
    """校验一天的全部硬约束，返回 (是否达标, 失败原因, 热量, 蛋白)"""
    reasons: list[str] = []

    if [meal.meal_type for meal in day.meals] != list(MEAL_ORDER):
        reasons.append("餐次不完整")
    if any(not (1 <= len(meal.items) <= 3) for meal in day.meals):
        reasons.append("每餐菜品数不在 1~3")

    names: list[str] = []
    for meal in day.meals:
        for item in meal.items:
            names.append(item.name)
            food = FOODS.get(item.name)
            if food is None:
                reasons.append(f"菜品不在营养库：{item.name}")
                continue
            ratio = item.portion_g / float(food["common_portion_g"])
            if not (1 <= round(ratio) <= 4) or abs(ratio - round(ratio)) > 1e-6:
                reasons.append(f"份量不是 1~4 份标准量：{item.name} {item.portion_g}g")
    if len(names) != len(set(names)):
        reasons.append("同一道菜一天内重复出现")

    for meal in day.meals:
        if meal.meal_type not in ("lunch", "dinner"):
            continue
        if not any(
            FOODS.get(item.name, {}).get("category") == "荤菜"
            for item in meal.items
        ):
            reasons.append(f"{meal.meal_type} 没有荤菜")

    total_calories = total_protein = 0.0
    for meal in day.meals:
        calories, protein = _nutrition(meal)
        total_calories += calories
        total_protein += protein
        if abs(calories - meal.calories) > 0.11:
            reasons.append(f"{meal.meal_type} 热量未按营养库重算")

    if abs(total_calories - TARGET_KCAL) > TARGET_KCAL * 0.07 + 1e-6:
        reasons.append("全天热量超出 ±7%")
    if total_protein + 1e-6 < required_protein:
        reasons.append(f"蛋白不足（{total_protein:.1f} < {required_protein:.1f}g）")

    return not reasons, reasons, total_calories, total_protein


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    all_ok = True

    for item in PROFILES:
        label = item["label"]
        profile = item["profile_ctx"]
        factor = PROTEIN_FACTORS[profile["goal"]]
        required = profile["weight_kg"] * factor

        print(f"\n===== {label} =====")
        print(f"热量目标 {TARGET_KCAL} kcal（允许 ±7%）；蛋白下限 {factor} g/kg × {profile['weight_kg']}kg = {required:.1f} g")

        started = perf_counter()
        plan = build_plan(profile, DAYS)
        elapsed = perf_counter() - started

        passed_days = 0
        for day in plan.days:
            ok, reasons, calories, protein = check_day(day, required)
            passed_days += int(ok)
            all_ok = all_ok and ok
            deviation = calories - TARGET_KCAL
            print(
                f"D{day.day_index}  "
                f"热量 {calories:7.1f} kcal  "
                f"偏差 {deviation:+6.1f} kcal ({deviation / TARGET_KCAL * 100:+6.2f}%)  "
                f"蛋白 {protein:6.1f}/{required:5.1f} g  "
                f"{'达标 ✓' if ok else '未达标 ✗ ' + '；'.join(reasons)}"
            )

        print(f"达标天数: {passed_days}/{DAYS}    生成耗时: {elapsed:.2f}s")

    print(f"\n{'全部达标 ✓' if all_ok else '存在未达标项 ✗'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
