"""多菜识别：识别结果拆成多道菜、逐道确认、合计口径"""
from app.routers.recognitions import _dish_views, _totals
from app.services.llm.mock_provider import MockProvider
from app.services.llm.schemas import RecognizedDish, VisionRecognitionResult


def _mock_multi():
    provider = MockProvider()
    for size in range(1, 400):
        vision = provider.recognize_food(b"x" * size, "image/jpeg", {})
        if len(vision.dishes) > 1:
            return vision
    return None


def test_mock_can_return_multiple_dishes():
    vision = _mock_multi()
    assert vision is not None, "mock 应该能产出多菜场景（1/3 概率）"
    assert 2 <= len(vision.dishes) <= 4
    names = [d.dish_name for d in vision.dishes]
    assert len(set(names)) == len(names)
    # 顶层字段与主菜一致（兼容旧逻辑）
    assert vision.dish_name == names[0]


def test_dish_views_fallback_for_single_dish():
    vision = VisionRecognitionResult(is_food=True, dish_name="宫保鸡丁", portion_g=260)
    views = _dish_views(vision)
    assert len(views) == 1
    assert views[0].dish_name == "宫保鸡丁"
    assert views[0].portion_g == 260


def test_totals_sum_and_note():
    dishes = [
        {"nutrition": {"calories": 300, "protein_g": 10, "fat_g": 5, "carb_g": 40, "matched": True}},
        {"nutrition": {"calories": 120, "protein_g": 15, "fat_g": 2, "carb_g": 1, "matched": True}},
    ]
    total = _totals(dishes)
    assert total["calories"] == 420
    assert total["protein_g"] == 25
    assert total["matched"] is True
    assert "2 道菜" in total["note"]


def test_totals_flags_unmatched_dish():
    dishes = [
        {"nutrition": {"calories": 300, "protein_g": 10, "fat_g": 5, "carb_g": 40, "matched": True}},
        {"nutrition": {"calories": 100, "protein_g": 1, "fat_g": 1, "carb_g": 20, "matched": False}},
    ]
    assert _totals(dishes)["matched"] is False
