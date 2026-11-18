"""依据一致性：每个数字都要能解释，且不许把工程取值说成指南原文"""
from app.services import plan_targets

CTX = {
    "height_cm": 172, "weight_kg": 78, "age": 26, "gender": "male",
    "activity_level": "moderate", "goal": "lose",
}

# 必须有依据的数值键（文本键如 bedtime/goal 不需要）
NUMERIC_KEYS = (
    "calorie_target", "calorie_min", "calorie_max",
    "protein_min_g", "protein_max_g", "fat_min_g", "fat_max_g",
    "carb_min_g", "carb_max_g", "fiber_min_g", "sugar_max_g", "sodium_max_mg",
    "water_min_ml", "water_max_ml", "sleep_min_h", "sleep_max_h",
    "exercise_burn_min", "exercise_burn_max", "exercise_days_per_week",
    "steps_min", "steps_max",
)


def _targets(**over):
    ctx = dict(CTX)
    ctx.update(over)
    return plan_targets.build_targets(ctx)


def test_every_numeric_target_has_source():
    t = _targets()
    sources = t["_source"]
    missing = [k for k in NUMERIC_KEYS if k not in sources]
    assert not missing, f"这些目标没有依据：{missing}"


def test_source_shape():
    for key, src in _targets()["_source"].items():
        assert src["kind"] in ("guideline", "derived", "heuristic"), key
        assert src["id"], key
        assert src["label"], key
        assert src["basis"], key


def test_heuristic_never_cites_guideline():
    """经验取值不许写成「依据《XX》」——这是最容易被追问倒的地方"""
    for key, src in _targets()["_source"].items():
        if src["kind"] == "heuristic":
            text = f"{src['label']}{src['basis']}"
            assert "依据《" not in text, f"{key} 是工程取值，却在引用指南：{text}"


def test_targets_carry_prescription():
    t = _targets()
    p = t["prescription"]
    assert p["target"] == t["calorie_target"]
    assert p["train_days"] == t["exercise_days_per_week"]
    assert p["summary"]


def test_healthy_target_equals_tdee():
    t = _targets(goal="healthy", weight_kg=78)
    assert abs(t["calorie_target"] - t["tdee"]) <= 5   # 目标取整到 10
    assert t["prescription"]["deficit_pct"] == 0


def test_protein_floor_applies_to_small_body():
    """体重很小但蛋白系数低时，不能低于 DRIs 绝对量"""
    t = _targets(weight_kg=45, height_cm=155, gender="female")
    assert t["protein_min_g"] >= 55


def test_calorie_band_is_not_a_single_line():
    """区间必须留出可操作的宽度，20% 赤字也不能压成一条线"""
    t = _targets(weight_kg=84)      # 20% 赤字，BMR 地板会顶到目标附近
    assert t["calorie_max"] - t["calorie_min"] >= 100
    assert t["calorie_min"] <= t["calorie_target"] <= t["calorie_max"]
