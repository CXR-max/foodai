import json
import logging
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path

from app.services.food_text import normalize_name

logger = logging.getLogger("llm")

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SEED_FILE = DATA_DIR / "foods_seed.json"
LEGACY_FILE = DATA_DIR / "foods_data.json"
ALIAS_FILE = DATA_DIR / "food_aliases.json"
RELATION_FILE = DATA_DIR / "food_relations.json"

CACHE_TTL_SECONDS = 60

_lock = threading.RLock()
_cache: list[dict] | None = None
_cache_at = 0.0
_name_map: dict[str, dict] = {}
_alias_map: dict[str, dict] = {}
_relations_by_name: dict[str, list[dict]] = {}


def _read_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _nutrient_dict(source: dict | None) -> dict:
    source = source or {}
    return {
        "calories": source.get("calories", source.get("energy_kcal")),
        "protein_g": source.get("protein_g"),
        "fat_g": source.get("fat_g"),
        "carb_g": source.get("carb_g"),
        "fiber_g": source.get("fiber_g"),
        "sodium_mg": source.get("sodium_mg"),
        "sugar_g": source.get("sugar_g"),
    }


def _normalize_entry(entry: dict) -> dict:
    food = dict(entry)
    nutrition = food.get("nutrition")
    per_100g = food.get("per_100g") or nutrition or {}
    food["per_100g"] = _nutrient_dict(per_100g)
    food["name"] = food.get("name") or food.get("name_zh") or ""
    food["name_norm"] = food.get("name_norm") or normalize_name(food["name"])
    food["category"] = food.get("category") or food.get("category_l1") or "其他"
    food["common_portion_g"] = float(food.get("common_portion_g") or food.get("serving_g") or 100)
    food["ingredients"] = food.get("ingredients") or []
    food["suitable_meals"] = food.get("suitable_meals") or []
    food["taste_tags"] = food.get("taste_tags") or []
    food["dietary_tags"] = food.get("dietary_tags") or []
    food["food_type"] = food.get("food_type") or "dish"
    food["dish_style"] = food.get("dish_style") or "家常菜"
    food["region"] = food.get("region") or "通用"
    food["cuisine"] = food.get("cuisine") or "家常菜"
    food["state"] = food.get("state") or "cooked"
    food["edible_ratio"] = float(food.get("edible_ratio") or 1.0)
    food["serving_g"] = float(food.get("serving_g") or food["common_portion_g"])
    food["aliases"] = food.get("aliases") or []
    food["relations"] = food.get("relations") or []
    return food


def _entry_from_db(food) -> dict:
    return _normalize_entry({
        "id": food.id,
        "source": food.source,
        "source_id": food.source_id,
        "name": food.name_zh,
        "name_en": food.name_en,
        "name_norm": food.name_norm,
        "food_type": food.food_type,
        "dish_style": food.dish_style,
        "region": food.region,
        "cuisine": food.cuisine,
        "category": food.category_l1,
        "category_l2": food.category_l2,
        "state": food.state,
        "edible_ratio": food.edible_ratio,
        "serving_g": food.serving_g,
        "common_portion_g": food.common_portion_g,
        "per_100g": {
            "calories": food.energy_kcal,
            "protein_g": food.protein_g,
            "fat_g": food.fat_g,
            "carb_g": food.carb_g,
            "fiber_g": food.fiber_g,
            "sodium_mg": food.sodium_mg,
            "sugar_g": food.sugar_g,
        },
        "ingredients": food.ingredients,
        "suitable_meals": food.suitable_meals,
        "taste_tags": food.taste_tags,
        "dietary_tags": food.dietary_tags,
        "source_type": food.source_type,
        "license": food.license,
        "source_url": food.source_url,
        "version": food.version,
        "verified": food.verified,
        "active": food.active,
    })


def _load_from_db() -> list[dict] | None:
    try:
        from app.db.session import SessionLocal
        from app.models.food import Food, FoodAlias, FoodRelation

        session = SessionLocal()
        try:
            rows = session.query(Food).filter(Food.active.is_(True)).order_by(Food.id.asc()).all()
            if not rows:
                return None
            foods = [_entry_from_db(row) for row in rows]
            by_id = {food["id"]: food for food in foods if food.get("id") is not None}
            for alias in session.query(FoodAlias).all():
                target = by_id.get(alias.food_id)
                if target is not None:
                    target["aliases"].append(alias.alias)
            relations = []
            for relation in session.query(FoodRelation).order_by(FoodRelation.priority.desc()).all():
                item = {
                    "group_key": relation.group_key,
                    "relation_type": relation.relation_type,
                    "name": relation.name,
                    "name_norm": relation.name_norm,
                    "canonical_name": relation.canonical_name,
                    "distinguish_tip": relation.distinguish_tip,
                    "nutrition_delta": relation.nutrition_delta,
                    "priority": relation.priority,
                    "source": relation.source,
                }
                relations.append(item)
                for food in foods:
                    if food["name_norm"] == relation.name_norm:
                        food["relations"].append(item)
            return _build_indexes(foods, relations)
        finally:
            session.close()
    except Exception as exc:
        logger.debug("营养库数据库加载失败，回退 JSON：%s", exc)
        return None


def _load_from_json() -> list[dict]:
    if SEED_FILE.exists():
        raw_foods = _read_json(SEED_FILE)
    else:
        raw_foods = _read_json(LEGACY_FILE)
    foods = [_normalize_entry(item) for item in raw_foods]
    aliases = _read_json(ALIAS_FILE) if ALIAS_FILE.exists() else []
    relations = _read_json(RELATION_FILE) if RELATION_FILE.exists() else []
    alias_by_name: dict[str, list[str]] = {}
    for alias in aliases:
        alias_by_name.setdefault(alias["name"], []).append(alias["alias"])
    for food in foods:
        food["aliases"] = list(dict.fromkeys(food["aliases"] + alias_by_name.get(food["name"], [])))
    for relation in relations:
        for food in foods:
            if food["name_norm"] == normalize_name(relation["name"]):
                food["relations"].append(relation)
    return _build_indexes(foods, relations)


def _build_indexes(foods: list[dict], relations: list[dict]) -> list[dict]:
    global _name_map, _alias_map, _relations_by_name
    name_map: dict[str, dict] = {}
    alias_map: dict[str, dict] = {}
    relations_by_name: dict[str, list[dict]] = {}
    for food in foods:
        name_map.setdefault(food["name_norm"], food)
        for alias in food.get("aliases", []):
            alias_map.setdefault(normalize_name(alias), food)
    for relation in relations:
        key = normalize_name(relation.get("name"))
        relations_by_name.setdefault(key, []).append(relation)
        food = name_map.get(key) or alias_map.get(key)
        if food is not None and relation not in food["relations"]:
            food["relations"].append(relation)
    _name_map = name_map
    _alias_map = alias_map
    _relations_by_name = relations_by_name
    return foods


def _load() -> list[dict]:
    global _cache, _cache_at
    with _lock:
        now = time.monotonic()
        if _cache is not None and now - _cache_at < CACHE_TTL_SECONDS:
            return _cache
        foods = _load_from_db()
        if foods is None:
            foods = _load_from_json()
        _cache = foods
        _cache_at = now
        return foods


def reload() -> None:
    global _cache, _cache_at
    with _lock:
        _cache = None
        _cache_at = 0.0


def load_foods() -> list[dict]:
    return _load()


def load_all_foods() -> list[dict]:
    try:
        from app.db.session import SessionLocal
        from app.models.food import Food, FoodAlias, FoodRelation

        session = SessionLocal()
        try:
            rows = session.query(Food).order_by(Food.id.asc()).all()
            if not rows:
                return _load_from_json()
            foods = [_entry_from_db(row) for row in rows]
            by_id = {food["id"]: food for food in foods if food.get("id") is not None}
            by_norm = {food["name_norm"]: food for food in foods}
            for alias in session.query(FoodAlias).all():
                target = by_id.get(alias.food_id)
                if target is not None:
                    target["aliases"].append(alias.alias)
            for relation in session.query(FoodRelation).all():
                target = by_norm.get(relation.name_norm)
                if target is not None:
                    target["relations"].append({
                        "group_key": relation.group_key,
                        "relation_type": relation.relation_type,
                        "name": relation.name,
                        "name_norm": relation.name_norm,
                        "canonical_name": relation.canonical_name,
                        "distinguish_tip": relation.distinguish_tip,
                        "nutrition_delta": relation.nutrition_delta,
                        "priority": relation.priority,
                        "source": relation.source,
                    })
            return foods
        finally:
            session.close()
    except Exception:
        return _load_from_json()


def get_library_stats() -> dict:
    foods = _load()
    all_foods = load_all_foods()
    cuisines: dict[str, int] = {}
    food_types: dict[str, int] = {}
    sources: dict[str, int] = {}
    for food in all_foods:
        cuisines[food["cuisine"]] = cuisines.get(food["cuisine"], 0) + 1
        food_types[food["food_type"]] = food_types.get(food["food_type"], 0) + 1
        source = food.get("source") or "unknown"
        sources[source] = sources.get(source, 0) + 1
    return {
        "total": len(foods),
        "total_all": len(all_foods),
        "inactive": max(len(all_foods) - len(foods), 0),
        "cuisines": dict(sorted(cuisines.items())),
        "food_types": dict(sorted(food_types.items())),
        "sources": dict(sorted(sources.items())),
    }


MATCH_METHOD_LABELS = {
    "exact": "精确命中",
    "synonym": "同义归一",
    "alias": "别名命中",
    "contains": "包含匹配",
    "fuzzy": "相似匹配",
}

# 相似度下限：低于它宁可不匹配（未收录 → 类别均值），避免"红烧鱼"硬配"红烧肉"
FUZZY_THRESHOLD = 0.72


def find_synonym(dish_name: str) -> dict | None:
    """菜名命中同义组时返回该关系（含 canonical_name）"""
    norm = normalize_name(dish_name)
    for relation in _relations_by_name.get(norm, []):
        if relation.get("relation_type") == "synonym" and relation.get("canonical_name"):
            return relation
    return None


def match_entry(dish_name: str) -> dict | None:
    """菜名 → 营养库条目 + 匹配方式，供上层判断是否需要用户确认

    返回：{entry, method, score, query, canonical_name, group_key}
    method: exact / synonym / alias / contains / fuzzy
    """
    name = (dish_name or "").strip()
    if not name:
        return None
    norm = normalize_name(name)
    foods = _load()

    def result(entry: dict, method: str, score: float, **extra) -> dict:
        return {
            "entry": entry,
            "method": method,
            "score": round(float(score), 3),
            "query": name,
            "canonical_name": entry["name"],
            "group_key": None,
            **extra,
        }

    exact = _name_map.get(norm)
    if exact is not None:
        return result(exact, "exact", 1.0)

    synonym = find_synonym(name)
    if synonym is not None:
        canonical_norm = normalize_name(synonym["canonical_name"])
        target = _name_map.get(canonical_norm) or _alias_map.get(canonical_norm)
        if target is not None:
            return result(target, "synonym", 0.99, group_key=synonym.get("group_key"))

    alias = _alias_map.get(norm)
    if alias is not None:
        return result(alias, "alias", 0.98)

    # 包含匹配：至少 3 个字，避免「番茄」命中「番茄意面配…」这种灾难性低估
    contained = [
        food for food in foods
        if food["name_norm"] and food["name_norm"] in norm and len(food["name_norm"]) >= 3
    ]
    if contained:
        best = max(contained, key=lambda item: (len(item["name_norm"]), item.get("food_type") == "dish"))
        coverage = len(best["name_norm"]) / len(norm) if norm else 0
        # 覆盖度低说明只是「菜名里出现了某个词」，分数压低，交给前端确认
        return result(best, "contains", 0.9 if coverage >= 0.6 else 0.82)

    best_food, best_ratio = None, 0.0
    for food in foods:
        ratio = SequenceMatcher(None, food["name_norm"], norm).ratio()
        if ratio > best_ratio:
            best_food, best_ratio = food, ratio
    if best_food is not None and best_ratio >= FUZZY_THRESHOLD:
        return result(best_food, "fuzzy", best_ratio)
    return None


def find_food(dish_name: str) -> dict | None:
    """兼容旧调用：只要条目"""
    match = match_entry(dish_name)
    return match["entry"] if match else None


def describe_match(match: dict | None) -> str:
    """把匹配结果转成一句人话，用于 note / 前端提示"""
    if not match:
        return "未匹配到营养库"
    label = MATCH_METHOD_LABELS.get(match["method"], match["method"])
    if match["method"] == "exact":
        return f"营养值来自本地营养库（{label}）"
    if match["method"] == "synonym":
        return f"已按「{match['canonical_name']}」记录（{label}）"
    return f"营养值来自本地营养库（{label} · {match['score']:.2f}）"


def confusable_relations(dish_name: str) -> list[dict]:
    """该菜所在的易混淆组（没有则空）"""
    match = match_entry(dish_name)
    if not match:
        return []
    return [r for r in match["entry"].get("relations", []) if r.get("relation_type") == "confusable"]


def find_candidates(dish_name: str, category: str | None = None, limit: int = 5) -> list[dict]:
    name = (dish_name or "").strip()
    if not name:
        return []
    norm = normalize_name(name)
    foods = _load()
    scored: dict[str, dict] = {}

    def add(food: dict, match_type: str, score: float, relation: dict | None = None):
        if category and food.get("category") != category:
            return
        key = food["name_norm"]
        current = scored.get(key)
        if current is None or score > current["score"]:
            scored[key] = {
                "entry": food,
                "match_type": match_type,
                "score": round(score, 3),
                "relation": relation,
            }

    exact = _name_map.get(norm)
    if exact is not None:
        add(exact, "exact", 1.0)
    synonym = find_synonym(name)
    if synonym is not None:
        canonical_norm = normalize_name(synonym["canonical_name"])
        target = _name_map.get(canonical_norm) or _alias_map.get(canonical_norm)
        if target is not None:
            add(target, "synonym", 0.99, synonym)
    alias = _alias_map.get(norm)
    if alias is not None:
        add(alias, "alias", 0.98)

    for food in foods:
        food_norm = food["name_norm"]
        if food_norm and food_norm in norm and len(food_norm) >= 2:
            add(food, "contains", 0.9)
        ratio = SequenceMatcher(None, food_norm, norm).ratio()
        if ratio >= 0.55:
            add(food, "fuzzy", ratio)

    for key, candidate in list(scored.items()):
        food = candidate["entry"]
        for relation in food.get("relations", []):
            if relation.get("relation_type") != "confusable":
                continue
            group = relation.get("group_key")
            for other in foods:
                if any(
                    rel.get("group_key") == group and rel.get("relation_type") == "confusable"
                    for rel in other.get("relations", [])
                ):
                    add(other, "confusable", 0.7, relation)

    results = list(scored.values())
    results.sort(key=lambda item: (-item["score"], -int(item["entry"].get("verified") or 0), len(item["entry"]["name"])))
    return results[:limit]


def get_relations(dish_name: str) -> list[dict]:
    _load()
    return list(_relations_by_name.get(normalize_name(dish_name), []))


def calc_nutrition(food: dict, portion_g: float) -> dict:
    per = food["per_100g"]
    factor = max(portion_g, 0) / 100.0
    return {
        "calories": round((per.get("calories") or 0) * factor, 1),
        "protein_g": round((per.get("protein_g") or 0) * factor, 1),
        "fat_g": round((per.get("fat_g") or 0) * factor, 1),
        "carb_g": round((per.get("carb_g") or 0) * factor, 1),
    }


def category_average(category: str | None = None) -> dict:
    foods = _load()
    pool = [f for f in foods if category and f.get("category") == category] or foods
    n = len(pool)
    return {
        "calories": round(sum((f["per_100g"].get("calories") or 0) for f in pool) / n, 1),
        "protein_g": round(sum((f["per_100g"].get("protein_g") or 0) for f in pool) / n, 1),
        "fat_g": round(sum((f["per_100g"].get("fat_g") or 0) for f in pool) / n, 1),
        "carb_g": round(sum((f["per_100g"].get("carb_g") or 0) for f in pool) / n, 1),
    }
