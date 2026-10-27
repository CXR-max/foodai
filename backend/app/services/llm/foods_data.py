"""
营养库加载器：读取 app/data/foods_data.json（数据与代码分离，加菜只需编辑那个 JSON 文件）
提供：模糊匹配菜品 / 每100g→实际分量换算 / 类别均值兜底
"""
import json
from difflib import SequenceMatcher
from pathlib import Path

# foods_data.json 位于 backend/app/data/ 下（本文件在 llm/ 里，往上走 2 级到 app/）
DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "foods_data.json"

_cache: list[dict] | None = None


def load_foods() -> list[dict]:
    """读取营养库（带缓存；改了 JSON 想立即生效可调 reload()）"""
    global _cache
    if _cache is None:
        with open(DATA_FILE, encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


def reload() -> None:
    """清空缓存，下次读取时重新加载 JSON"""
    global _cache
    _cache = None


def find_food(dish_name: str) -> dict | None:
    """按菜名模糊匹配营养库：精确匹配 → 互相包含 → 相似度≥0.5"""
    name = (dish_name or "").strip()
    if not name:
        return None
    foods = load_foods()
    # 1) 精确匹配
    for food in foods:
        if food["name"] == name:
            return food
    # 2) 互相包含（如"一碗牛肉拉面"能匹配到"牛肉拉面"）
    for food in foods:
        if food["name"] in name or name in food["name"]:
            return food
    # 3) 相似度匹配（容忍小的识别误差）
    best, best_ratio = None, 0.0
    for food in foods:
        ratio = SequenceMatcher(None, food["name"], name).ratio()
        if ratio > best_ratio:
            best, best_ratio = food, ratio
    return best if best_ratio >= 0.5 else None


def calc_nutrition(food: dict, portion_g: float) -> dict:
    """每100g营养 × 实际分量 = 这一份的营养"""
    per = food["per_100g"]
    factor = max(portion_g, 0) / 100.0
    return {
        "calories": round(per["calories"] * factor, 1),
        "protein_g": round(per["protein_g"] * factor, 1),
        "fat_g": round(per["fat_g"] * factor, 1),
        "carb_g": round(per["carb_g"] * factor, 1),
    }


def category_average(category: str | None = None) -> dict:
    """某类别的 per_100g 均值；category 为空或没命中时用全库均值（未收录菜品的兜底）"""
    foods = load_foods()
    pool = [f for f in foods if category and f.get("category") == category] or foods
    n = len(pool)
    return {
        "calories": round(sum(f["per_100g"]["calories"] for f in pool) / n, 1),
        "protein_g": round(sum(f["per_100g"]["protein_g"] for f in pool) / n, 1),
        "fat_g": round(sum(f["per_100g"]["fat_g"] for f in pool) / n, 1),
        "carb_g": round(sum(f["per_100g"]["carb_g"] for f in pool) / n, 1),
    }
