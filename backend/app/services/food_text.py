import re
import unicodedata

_REGION_BY_CUISINE = {
    "川": "大陆",
    "鲁": "大陆",
    "粤": "大陆",
    "苏": "大陆",
    "浙": "大陆",
    "闽": "大陆",
    "湘": "大陆",
    "徽": "大陆",
    "东北": "大陆",
    "西北": "大陆",
    "滇": "大陆",
    "北方": "大陆",
    "京": "大陆",
    "沪": "大陆",
    "客家": "大陆",
    "清真": "大陆",
    "港式": "香港",
    "台式": "台湾",
    "韩式": "韩国",
    "日式": "日本",
    "西式": "通用",
    "轻食": "通用",
    "家常菜": "大陆",
    "通用": "通用",
}

_FOOD_TYPE_BY_CATEGORY = {
    "水果": "single_food",
    "饮品": "beverage",
}

_OPENCC = None
_OPENCC_TRIED = False


def _simplify(text: str) -> str:
    global _OPENCC, _OPENCC_TRIED
    if not _OPENCC_TRIED:
        _OPENCC_TRIED = True
        try:
            from opencc import OpenCC

            _OPENCC = OpenCC("t2s")
        except Exception:
            _OPENCC = None
    if _OPENCC is None:
        return text
    return _OPENCC.convert(text)


def normalize_name(name: str | None) -> str:
    text = unicodedata.normalize("NFKC", name or "").strip().lower()
    text = _simplify(text)
    text = re.sub(r"[\s·•,，、/]+", "", text)
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"^(一份|一碗|一盘|大份|小份|标准份|中份)", "", text)
    text = re.sub(r"(一份|一碗|一盘|大份|小份|标准份|中份)$", "", text)
    return text


def infer_region(cuisine: str | None) -> str:
    return _REGION_BY_CUISINE.get((cuisine or "").strip(), "通用")


def infer_food_type(category: str | None) -> str:
    return _FOOD_TYPE_BY_CATEGORY.get((category or "").strip(), "dish")


def canonical_nutrients(per_100g: dict | None) -> dict:
    per = per_100g or {}
    return {
        "calories": per.get("calories"),
        "protein_g": per.get("protein_g"),
        "fat_g": per.get("fat_g"),
        "carb_g": per.get("carb_g"),
        "fiber_g": per.get("fiber_g"),
        "sodium_mg": per.get("sodium_mg"),
        "sugar_g": per.get("sugar_g"),
    }
