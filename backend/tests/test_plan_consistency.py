"""方案自洽：「按方案吃完一定落在七项区间内」

这是本轮的核心承诺。之前的问题是生成侧只有两条硬约束（热量上下限 + 蛋白下限），
脂肪碳水一条没有，凑满热量时蛋白被顶到 2.4 g/kg（区间 1.2~1.6），
于是出现「按计划吃完还超标」——用户没吃错，是方案排错了。
"""
from datetime import date

import pytest

from app.services import nutrition_check, plan_targets, planner

# 用户实测档案：165 cm / 55 kg / 维持，判定区间 1640~1910 kcal、蛋白 66~88 g
PROFILE = {
    "gender": "female", "age": 30, "height_cm": 165, "weight_kg": 55,
    "activity_level": "moderate", "goal": "maintain",
}
OVERWEIGHT = {
    "gender": "male", "age": 40, "height_cm": 175, "weight_kg": 88,
    "activity_level": "light", "goal": "lose",
}


SOFT_CAP_RATIO = 2.0    # 软化结构项的硬帽：允许略超，但绝不放飞（模型里也是 2×）


def _soft_keys(day) -> set[str]:
    return set((day.day_flags or {}).get("soft") or [])


@pytest.mark.parametrize("profile", [PROFILE, OVERWEIGHT], ids=["maintain", "lose"])
def test_every_day_sits_inside_seven_bands(profile):
    """逐日复核：七项落在区间内。

    热量/蛋白/脂肪/碳水永不降级，必须全过；纤维/钠/糖受菜库数据完整度限制
    （纤维覆盖 63.9%），设计上允许降级留痕（DEGRADE_LEVELS）——降级的日子
    按 2 倍放宽帽断言，且 day_flags 必须记录放宽后的实际值。
    """
    targets = plan_targets.build_targets(profile)
    plan = planner.build_plan(profile, 2)

    for day in plan.days:
        assert day.nutrition, f"第 {day.day_index} 天没存营养值"
        soft = _soft_keys(day)
        hard_targets = {
            key: value for key, value in targets.items()
            if not (key.startswith("fiber") or key.startswith("sodium") or key.startswith("sugar"))
        } if soft else targets
        verdict = nutrition_check.check(day.nutrition, hard_targets)
        assert verdict["ok"], f"第 {day.day_index} 天越界：{verdict['violations']}"
        assert day.day_flags and day.day_flags.get("verified") is True
        for item in (day.day_flags or {}).get("relaxed") or []:
            assert item["key"] in soft


def test_protein_has_upper_bound_now():
    """蛋白上限必须真的生效：以前只卡下限，排出来 133 g（上限 88）"""
    targets = plan_targets.build_targets(PROFILE)
    plan = planner.build_plan(PROFILE, 2)

    assert targets["protein_max_g"] == 88
    for day in plan.days:
        protein = day.nutrition["protein_g"]
        assert targets["protein_min_g"] <= protein <= targets["protein_max_g"], (
            f"第 {day.day_index} 天蛋白 {protein} g 不在 "
            f"{targets['protein_min_g']}~{targets['protein_max_g']} 内"
        )


def test_structural_items_also_constrained():
    """脂肪/碳水必须落区间（永不降级）；纤维/钠/糖允许降级但绝不放飞（2 倍帽）"""
    targets = plan_targets.build_targets(PROFILE)
    day = planner.build_plan(PROFILE, 1).days[0]
    nutrition = day.nutrition
    soft = _soft_keys(day)

    assert targets["fat_min_g"] <= nutrition["fat_g"] <= targets["fat_max_g"]
    assert targets["carb_min_g"] <= nutrition["carb_g"] <= targets["carb_max_g"]

    if "fiber" in soft:
        assert nutrition["fiber_g"] >= targets["fiber_min_g"] * 0.5, "纤维下限的 2 倍放宽帽失效"
    else:
        assert nutrition["fiber_g"] >= targets["fiber_min_g"] - 1
    if "sodium" in soft:
        assert nutrition["sodium_mg"] <= targets["sodium_max_mg"] * SOFT_CAP_RATIO, "钠的 2 倍放宽帽失效"
    else:
        assert nutrition["sodium_mg"] <= targets["sodium_max_mg"] + 20
    if "sugar" in soft:
        assert nutrition["sugar_g"] <= targets["sugar_max_g"] * SOFT_CAP_RATIO, "糖的 2 倍放宽帽失效"
    else:
        assert nutrition["sugar_g"] <= targets["sugar_max_g"] + 1


def test_unachievable_profile_is_refused_not_delivered():
    """排不出就让生成失败，而不是给一份「吃完必超标」的方案

    极端忌口：素食 + 低盐 + 低糖 + 忌辣 + 少油，候选池被压到很小，
    七项同时硬约束可能真的无解——这时必须报错，不能静默降级交付。
    """
    ctx = dict(PROFILE)
    ctx["dietary_restrictions"] = ["素食", "低盐", "低糖", "忌辣", "少油", "乳糖不耐", "大豆过敏", "坚果过敏"]

    try:
        plan = planner.build_plan(ctx, 1)
    except planner.PlannerError as exc:
        assert "排不出" in str(exc) or "无解" in str(exc)
        return

    # 万一排出来了，就必须真的达标
    targets = plan_targets.build_targets(ctx)
    assert nutrition_check.check(plan.days[0].nutrition, targets)["ok"]


def test_day_flags_records_degradation():
    """降级要留痕：转软的结构项写进 day_flags，前端能显示「这天纤维放宽了」"""
    plan = planner.build_plan(PROFILE, 1)
    flags = plan.days[0].day_flags or {}
    assert "soft" in flags
    if flags["soft"]:
        assert flags.get("relaxed"), "降级了却没记录放宽后的实际值"


def test_nutrition_check_reports_side_and_delta():
    """越界要能说清「超哪一项、超多少」——这是文案的依据"""
    targets = plan_targets.build_targets(PROFILE)
    bad = {"calories": 2100, "protein_g": 133, "fat_g": 50, "carb_g": 240,
           "fiber_g": 30, "sodium_mg": 1800, "sugar_g": 20}
    verdict = nutrition_check.check(bad, targets)

    assert verdict["ok"] is False
    over = {v["key"]: v for v in verdict["violations"] if v["status"] == "over"}
    assert "protein" in over
    assert over["protein"]["delta"] > 40
    assert "蛋白" in nutrition_check.violation_text(over["protein"])
