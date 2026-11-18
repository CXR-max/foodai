"""诉求 → 处方：核心是「诉求决定策略，BMI/体脂只决定强度」，不按 BMI 一刀切"""
from app.services import plan_targets

BASE = {
    "height_cm": 172, "weight_kg": 78, "age": 26, "gender": "male",
    "activity_level": "moderate", "goal": "lose",
}


def _rx(**over):
    ctx = dict(BASE)
    ctx.update(over)
    return plan_targets.build_prescription(ctx)


def test_healthy_never_creates_deficit():
    """BMI 27 但只想健康饮食：不许偷偷扣热量"""
    rx = _rx(goal="healthy", weight_kg=78)
    assert rx["effective_intent"] == "healthy"
    assert rx["strategy"] == "maintain"
    assert rx["deficit_pct"] == 0
    assert abs(rx["target"] - rx["tdee"]) <= 5   # 目标取整到 10
    assert any("不设减重目标" in w for w in rx["warnings"])


def test_healthy_keeps_three_train_days():
    assert _rx(goal="healthy")["train_days"] == 3
    assert _rx(goal="lose")["train_days"] == 5


def test_normal_bmi_lose_is_allowed_but_slow():
    """标准身材也要减：允许，但只给 10% 并提示别只盯体重"""
    rx = _rx(weight_kg=65)          # BMI 22.0
    assert rx["effective_intent"] == "lose"
    assert rx["deficit_pct"] == 0.10
    assert any("正常范围" in w for w in rx["warnings"])


def test_normal_bmi_high_body_fat_gets_15pct():
    rx = _rx(weight_kg=65, body_fat_pct=24)
    assert rx["deficit_pct"] == 0.15


def test_overweight_lose_15pct():
    rx = _rx(weight_kg=78)          # BMI 26.4
    assert rx["deficit_pct"] == 0.15


def test_obese_lose_20pct():
    rx = _rx(weight_kg=84)          # BMI 28.4
    assert rx["deficit_pct"] == 0.20


def test_underweight_lose_refused():
    """BMI 17 还要减：按维持给，并明确劝阻"""
    rx = _rx(weight_kg=50)          # BMI 16.9
    assert rx["effective_intent"] == "maintain"
    assert rx["deficit_pct"] == 0
    assert any("偏低" in w for w in rx["warnings"])
    assert rx["summary"].startswith("维持热量")


def test_overweight_gain_gets_small_surplus():
    rx = _rx(goal="gain", weight_kg=84)
    assert rx["surplus_pct"] == 0.05
    assert any("同时增肌减脂" in w for w in rx["warnings"])


def test_protein_uses_ideal_weight_when_overweight():
    """BMI ≥24 蛋白按理想体重 22×身高²，不是实际体重"""
    rx = _rx(weight_kg=87, height_cm=170)
    assert rx["bmi"] >= 28
    assert 62 <= rx["basis_weight_kg"] <= 65      # 22 × 1.7² = 63.6
    t = plan_targets.build_targets(dict(BASE, weight_kg=87, height_cm=170))
    assert t["protein_min_g"] <= 110              # 旧算法（实际体重×1.6）会给 139
    assert "理想体重" in t["_source"]["protein_min_g"]["label"]


def test_rate_matches_actual_target():
    """每周减重必须由「目标 − TDEE」现算，不能写死"""
    for kw in ({"weight_kg": 78}, {"weight_kg": 65}, {"goal": "gain", "weight_kg": 70}):
        rx = _rx(**kw)
        expect = round((rx["target"] - rx["tdee"]) * 7 / 7700, 1)
        assert abs(rx["rate_kg_week"] - expect) <= 0.05
        assert rx["rate_text"]


def test_target_never_below_bmr_guard():
    rx = _rx(weight_kg=84, activity_level="sedentary")
    assert rx["target"] >= round(rx["bmr"] * 1.1 / 10) * 10
