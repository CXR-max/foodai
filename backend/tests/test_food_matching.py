"""匹配层 + 置信度判定单测（不联网、不调模型，验证"该确认的必须确认"）"""
from app.services import nutrition, recognition
from app.services.llm import foods_data


class FakeVision:
    def __init__(self, dish_name: str, confidence: float = 0.9, is_food: bool = True, portion_g: float = 200):
        self.dish_name = dish_name
        self.confidence = confidence
        self.is_food = is_food
        self.portion_g = portion_g


def test_exact_match():
    match = foods_data.match_entry("宫保鸡丁")
    assert match is not None
    assert match["method"] == "exact"
    assert match["score"] == 1.0


def test_synonym_match_maps_to_canonical():
    match = foods_data.match_entry("番茄炒蛋")
    assert match is not None
    assert match["method"] == "synonym"
    assert match["canonical_name"] == "西红柿炒鸡蛋"


def test_unknown_dish_is_not_force_matched():
    # 库里没有"红烧鱼"，不允许硬配成"红烧肉"（相似度 0.67 < 0.72）
    match = foods_data.match_entry("红烧鱼")
    assert match is None or match["method"] != "exact"


def test_high_confidence_exact_needs_no_confirm():
    info = recognition.evaluate(FakeVision("米饭", confidence=0.95))
    assert info["needs_confirm"] is False
    assert info["reason_codes"] == []
    assert info["matched"] is True


def test_low_confidence_requires_confirm_with_candidates():
    info = recognition.evaluate(FakeVision("宫保鸡丁", confidence=0.4))
    assert info["needs_confirm"] is True
    assert "low_confidence" in info["reason_codes"]
    assert info["candidates"]
    assert info["candidates"][0]["name"] == "宫保鸡丁"
    assert 0 < info["candidates"][0]["common_portion_g"] <= 3000


def test_confusable_group_requires_confirm():
    info = recognition.evaluate(FakeVision("红烧肉", confidence=0.95))
    assert "confusable" in info["reason_codes"]
    assert info["needs_confirm"] is True
    names = [item["name"] for item in info["candidates"]]
    assert "东坡肉" in names


def test_unmatched_requires_confirm():
    info = recognition.evaluate(FakeVision("泰式冬阴功汤", confidence=0.9))
    assert "unmatched" in info["reason_codes"]
    assert info["needs_confirm"] is True


def test_match_and_calc_reports_method_and_source():
    nut = nutrition.match_and_calc("番茄炒蛋", 200)
    assert nut["matched"] is True
    assert nut["match_method"] == "synonym"
    assert nut["nutrition_source"] == "dish_library"
    assert nut["calories"] > 0


def test_unmatched_falls_back_to_category_average():
    nut = nutrition.match_and_calc("泰式冬阴功汤", 250, "汤粥")
    assert nut["matched"] is False
    assert nut["nutrition_source"] == "category_average"
    assert nut["calories"] > 0
