import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.food_text import (  # noqa: E402
    canonical_nutrients,
    infer_food_type,
    infer_region,
    normalize_name,
)

DATA_DIR = ROOT / "app" / "data"
LEGACY_FILE = DATA_DIR / "foods_data.json"
EXTRA_FILE = DATA_DIR / "foods_extra.json"
META_FILE = DATA_DIR / "food_meta.json"
ALIAS_FILE = DATA_DIR / "food_aliases.json"
RELATION_FILE = DATA_DIR / "food_relations.json"
OUTPUT_FILE = DATA_DIR / "foods_seed.json"

CATEGORY_STYLE = {
    "主食": "主食",
    "荤菜": "家常菜",
    "素菜": "家常菜",
    "汤粥": "汤粥",
    "早餐": "早餐",
    "小吃": "小吃",
    "水果": "单品",
    "饮品": "饮品",
}


def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_food(record: dict, meta: dict, source: str) -> dict:
    name = record["name"]
    category = record.get("category", "其他")
    cuisine = record.get("cuisine") or meta.get("cuisine", {}).get(name, "家常菜")
    food_type = record.get("food_type") or meta.get("food_type", {}).get(name) or infer_food_type(category)
    dish_style = record.get("dish_style") or meta.get("dish_style", {}).get(name) or CATEGORY_STYLE.get(category, "家常菜")
    state = record.get("state") or meta.get("state", {}).get(name, "cooked")
    portion = float(record.get("common_portion_g") or 100)
    return {
        "source": source,
        "source_id": name,
        "name_zh": name,
        "name_en": record.get("name_en"),
        "name_norm": normalize_name(name),
        "food_type": food_type,
        "dish_style": dish_style,
        "region": record.get("region") or infer_region(cuisine),
        "cuisine": cuisine,
        "category_l1": category,
        "category_l2": record.get("category_l2"),
        "state": state,
        "edible_ratio": float(record.get("edible_ratio") or 1.0),
        "serving_g": float(record.get("serving_g") or portion),
        "common_portion_g": portion,
        "nutrition": canonical_nutrients(record.get("per_100g")),
        "extra_nutrients": record.get("extra_nutrients"),
        "ingredients": record.get("ingredients") or [],
        "suitable_meals": record.get("suitable_meals") or [],
        "taste_tags": record.get("taste_tags") or [],
        "dietary_tags": record.get("dietary_tags") or [],
        "source_type": record.get("source_type") or ("legacy_seed" if source == "legacy_json" else "seed_estimate"),
        "license": record.get("license") or ("internal-seed" if source == "legacy_json" else "app-seed"),
        "source_url": record.get("source_url"),
        "version": record.get("version") or "1",
        "verified": bool(record.get("verified", False)),
        "active": bool(record.get("active", True)),
    }


def validate(foods: list[dict], aliases: list[dict], relations: list[dict]) -> None:
    names = {f["name_zh"] for f in foods}
    norms = Counter(f["name_norm"] for f in foods)
    duplicates = [name for name, count in norms.items() if count > 1]
    if duplicates:
        raise ValueError(f"duplicate normalized food names: {duplicates}")
    missing_aliases = [a for a in aliases if a["name"] not in names]
    if missing_aliases:
        raise ValueError(f"aliases with missing target: {missing_aliases}")
    alias_names = {a["alias"] for a in aliases}
    for relation in relations:
        relation_type = relation["relation_type"]
        if relation_type == "confusable" and relation["name"] not in names:
            raise ValueError(f"confusable name missing: {relation}")
        if relation_type == "synonym" and relation["name"] not in names and relation["name"] not in alias_names:
            raise ValueError(f"synonym name missing: {relation}")
        canonical = relation.get("canonical_name")
        if canonical and canonical not in names:
            raise ValueError(f"relation canonical missing: {relation}")
    errors = []
    for food in foods:
        nut = food["nutrition"]
        energy = nut.get("calories")
        protein = nut.get("protein_g") or 0
        fat = nut.get("fat_g") or 0
        carb = nut.get("carb_g") or 0
        if energy is None or energy < 0 or energy > 900:
            errors.append(f"{food['name_zh']}: energy out of range")
        if not (0 <= protein <= 100 and 0 <= fat <= 100 and 0 <= carb <= 100):
            errors.append(f"{food['name_zh']}: macro out of range")
        estimated = 4 * protein + 9 * fat + 4 * carb
        if energy and abs(estimated - energy) > max(60, energy * 0.35):
            errors.append(f"{food['name_zh']}: macro-energy mismatch {energy} vs {estimated:.0f}")
        if food["common_portion_g"] <= 0:
            errors.append(f"{food['name_zh']}: invalid portion")
    if errors:
        raise ValueError("\n".join(errors))


def main() -> None:
    legacy = load_json(LEGACY_FILE)
    extra = load_json(EXTRA_FILE)
    meta = load_json(META_FILE)
    aliases = load_json(ALIAS_FILE)
    relations = load_json(RELATION_FILE)

    foods = [build_food(record, meta, "legacy_json") for record in legacy]
    foods.extend(build_food(record, meta, "app_seed") for record in extra)
    validate(foods, aliases, relations)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(foods, f, ensure_ascii=False, indent=2)

    cuisines = Counter(f["cuisine"] for f in foods)
    types = Counter(f["food_type"] for f in foods)
    print(f"wrote {len(foods)} foods to {OUTPUT_FILE}")
    print("cuisines:", dict(sorted(cuisines.items())))
    print("types:", dict(sorted(types.items())))
    print(f"aliases={len(aliases)} relations={len(relations)}")


if __name__ == "__main__":
    main()
