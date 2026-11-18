"""饮食偏离归因：先分清「方案错」还是「自己多吃」，再决定说什么

四种情形必须说出四句不同的话：
- 按方案吃完仍超 → 方案的问题，不要求用户少吃
- 额外加餐导致超 → 用户的选择，给出口但不指责
- 都打卡仍偏低   → 先问「吃不下还是没时间」，再改方案内容
- 还有餐没打     → 「进行中」，不判红
"""
import pytest

from app.services import metric_bands

TARGETS = {
    "calorie_target": 1821, "calorie_min": 1640, "calorie_max": 1910,
    "protein_min_g": 66, "protein_max_g": 88,
    "fat_min_g": 40, "fat_max_g": 61,
    "carb_min_g": 205, "carb_max_g": 250,
    "fiber_min_g": 25, "sodium_max_mg": 2000, "sugar_max_g": 25,
}

ZERO = {"calories": 0, "protein_g": 0, "fat_g": 0, "carb_g": 0,
        "fiber_g": 0, "sodium_mg": 0, "sugar_g": 0}

OK_PLAN = {"calories": 1830, "protein_g": 80, "fat_g": 52, "carb_g": 235,
           "fiber_g": 29, "sodium_mg": 1800, "sugar_g": 18}


def make_intake(plan: dict, extra: dict | None = None, records: int = 3) -> dict:
    extra = extra or dict(ZERO)
    total = {key: round(plan[key] + extra[key], 1) for key in ZERO}
    return {**total, "records": records, "plan": plan, "extra": extra}


def test_plan_defect_is_blamed_on_the_plan():
    """按方案吃完仍超：文案必须说「方案的问题，不是你的问题」"""
    bad_plan = dict(OK_PLAN, protein_g=133)     # 老方案排到 2.4 g/kg
    diet = metric_bands.diet_summary(TARGETS, intake=make_intake(bad_plan))

    assert diet["status"] == "plan_over"
    assert diet["attribution"] == "plan"
    assert "这是方案的问题" in diet["hint"]
    assert "下次生成会收紧" in diet["hint"]
    # 绝不能出现「下一餐减一点」这种把锅甩给用户的话
    assert "下一餐减一点" not in diet["hint"]


def test_extra_food_over_is_not_blamed_on_user():
    """额外加餐导致超：给出口，不指责"""
    extra = dict(ZERO, calories=520, protein_g=8, fat_g=20, carb_g=60, sugar_g=20)
    diet = metric_bands.diet_summary(TARGETS, intake=make_intake(OK_PLAN, extra))

    assert diet["status"] == "calorie_over"
    assert diet["attribution"] == "extra"
    assert "明天照常吃就行" in diet["hint"]
    for word in ("吃太多", "自律", "不该"):
        assert word not in diet["hint"]


def test_under_after_all_meals_asks_a_question():
    """四餐都打卡了仍低：先问是吃不下还是没时间，而不是直接给建议"""
    light = dict(OK_PLAN, calories=1400, protein_g=58, fat_g=34, carb_g=160, fiber_g=20)
    diet = metric_bands.diet_summary(TARGETS, intake=make_intake(light))

    assert diet["status"] == "calorie_under"
    assert diet["attribution"] == "intake"
    assert "吃不下" in diet["hint"] and "没时间" in diet["hint"]


def test_pending_meals_stays_in_progress():
    """还有餐没打卡：只是没吃完，不能判不足"""
    half = dict(OK_PLAN, calories=600, protein_g=25, fat_g=18, carb_g=70, fiber_g=9)
    diet = metric_bands.diet_summary(
        TARGETS, intake=make_intake(half, records=1), pending_meals=2,
        pending_labels=["晚餐", "加餐"],
    )

    assert diet["status"] == "in_progress"
    assert "没打卡" in diet["hint"]
    assert diet["calorie"]["status"] == "under"      # 单行仍是不足，但汇总不判红


def test_all_seven_in_range_is_ok():
    diet = metric_bands.diet_summary(TARGETS, intake=make_intake(OK_PLAN))

    assert diet["status"] == "ok"
    assert diet["ok"] is True
    assert [item["key"] for item in diet["items"]] == [
        "calorie", "protein", "fat", "carb", "fiber", "sodium", "sugar",
    ]
    assert {item["status"] for item in diet["items"]} == {"in_range"}


def test_no_records_is_no_data_not_under():
    """还没吃 ≠ 吃了 0"""
    diet = metric_bands.diet_summary(TARGETS, intake={**ZERO, "records": 0})
    assert diet["status"] == "no_data"


def test_structure_row_shows_fat_carb_fiber():
    """结构项折叠成一行，不卡打卡但用户看得见"""
    diet = metric_bands.diet_summary(TARGETS, intake=make_intake(OK_PLAN))
    keys = {item["key"] for item in diet["structure"]}
    assert keys == {"fat", "carb", "fiber", "sodium", "sugar"}
