"""禁忌/过敏提示：后端按档案算，前端直接用（前后端口径一致）"""
from app.services.recognition import dietary_warnings


def _dish(name, ingredients, fat=10.0, portion=200.0, tags=None):
    return {
        "dish_name": name,
        "ingredients": [{"name": i} for i in ingredients],
        "dietary_tags": tags or [],
        "portion_g": portion,
        "nutrition": {"fat_g": fat},
    }


def test_no_restrictions_no_warning():
    assert dietary_warnings(_dish("宫保鸡丁", ["花生", "鸡胸肉"]), []) == []


def test_peanut_allergy_hits():
    hits = dietary_warnings(_dish("宫保鸡丁", ["花生", "鸡胸肉"]), ["花生过敏"])
    assert any("花生过敏" in h and "花生" in h for h in hits)


def test_seafood_allergy_hits():
    hits = dietary_warnings(_dish("白灼虾", ["虾"]), ["海鲜过敏"])
    assert any("海鲜过敏" in h for h in hits)


def test_vegetarian_flags_meat():
    hits = dietary_warnings(_dish("回锅肉", ["五花肉", "青椒"]), ["素食"])
    assert any("素食" in h for h in hits)


def test_high_fat_flags_oily():
    hits = dietary_warnings(_dish("红烧肉", ["五花肉"], fat=40, portion=200), ["少油"])
    assert any("少油" in h for h in hits)


def test_low_fat_not_flagged():
    hits = dietary_warnings(_dish("蒜蓉西兰花", ["西兰花"], fat=3, portion=250), ["少油"])
    assert hits == []
