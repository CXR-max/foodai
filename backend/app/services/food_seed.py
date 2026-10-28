import hashlib
import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.food import Food, FoodAlias, FoodRelation, ImportBatch
from app.services.food_text import normalize_name
from app.services.llm import foods_data

logger = logging.getLogger("food_seed")

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SEED_FILE = DATA_DIR / "foods_seed.json"
ALIAS_FILE = DATA_DIR / "food_aliases.json"
RELATION_FILE = DATA_DIR / "food_relations.json"


def _read_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _food_values(item: dict) -> dict:
    nutrition = item.get("nutrition") or item.get("per_100g") or {}
    return {
        "source": item["source"],
        "source_id": item["source_id"],
        "name_zh": item["name_zh"],
        "name_en": item.get("name_en"),
        "name_norm": item.get("name_norm") or normalize_name(item["name_zh"]),
        "food_type": item.get("food_type") or "dish",
        "dish_style": item.get("dish_style") or "家常菜",
        "region": item.get("region") or "通用",
        "cuisine": item.get("cuisine") or "家常菜",
        "category_l1": item.get("category_l1") or "其他",
        "category_l2": item.get("category_l2"),
        "state": item.get("state") or "cooked",
        "edible_ratio": item.get("edible_ratio") or 1.0,
        "serving_g": item.get("serving_g"),
        "common_portion_g": item.get("common_portion_g") or 100,
        "energy_kcal": nutrition.get("calories", nutrition.get("energy_kcal")),
        "protein_g": nutrition.get("protein_g"),
        "fat_g": nutrition.get("fat_g"),
        "carb_g": nutrition.get("carb_g"),
        "fiber_g": nutrition.get("fiber_g"),
        "sodium_mg": nutrition.get("sodium_mg"),
        "sugar_g": nutrition.get("sugar_g"),
        "extra_nutrients": item.get("extra_nutrients"),
        "ingredients": item.get("ingredients"),
        "suitable_meals": item.get("suitable_meals"),
        "taste_tags": item.get("taste_tags"),
        "dietary_tags": item.get("dietary_tags"),
        "source_type": item.get("source_type"),
        "license": item.get("license"),
        "source_url": item.get("source_url"),
        "version": item.get("version"),
        "verified": item.get("verified", False),
        "active": item.get("active", True),
    }


def _upsert_food(db: Session, item: dict, force: bool) -> tuple[Food, bool]:
    values = _food_values(item)
    food = (
        db.query(Food)
        .filter(Food.source == values["source"], Food.source_id == values["source_id"])
        .first()
    )
    if food is None:
        food = Food(**values)
        db.add(food)
        db.flush()
        return food, True
    if force:
        for key, value in values.items():
            setattr(food, key, value)
        db.flush()
    return food, False


def seed_foods_from_json(force: bool = False) -> dict:
    foods = _read_json(SEED_FILE)
    aliases = _read_json(ALIAS_FILE) if ALIAS_FILE.exists() else []
    relations = _read_json(RELATION_FILE) if RELATION_FILE.exists() else []
    checksum = hashlib.sha256(SEED_FILE.read_bytes()).hexdigest()

    db = SessionLocal()
    inserted = updated = 0
    try:
        foods_by_name: dict[str, Food] = {}
        for item in foods:
            food, created = _upsert_food(db, item, force)
            foods_by_name[food.name_zh] = food
            inserted += int(created)
            updated += int(not created and force)

        for alias in aliases:
            target = foods_by_name.get(alias["name"])
            if target is None:
                target = db.query(Food).filter(Food.name_zh == alias["name"]).first()
            if target is None:
                continue
            alias_norm = normalize_name(alias["alias"])
            exists = (
                db.query(FoodAlias)
                .filter(FoodAlias.food_id == target.id, FoodAlias.alias_norm == alias_norm)
                .first()
            )
            if exists is None:
                db.add(FoodAlias(
                    food_id=target.id,
                    alias=alias["alias"],
                    alias_norm=alias_norm,
                    alias_type=alias.get("alias_type") or "regional",
                    source="app_seed",
                ))

        for relation in relations:
            name_norm = normalize_name(relation["name"])
            exists = (
                db.query(FoodRelation)
                .filter(
                    FoodRelation.group_key == relation["group_key"],
                    FoodRelation.relation_type == relation["relation_type"],
                    FoodRelation.name_norm == name_norm,
                )
                .first()
            )
            if exists is not None and not force:
                continue
            canonical = foods_by_name.get(relation.get("canonical_name") or "")
            if canonical is None and relation.get("canonical_name"):
                canonical = db.query(Food).filter(Food.name_zh == relation["canonical_name"]).first()
            values = {
                "group_key": relation["group_key"],
                "relation_type": relation["relation_type"],
                "name": relation["name"],
                "name_norm": name_norm,
                "canonical_name": relation.get("canonical_name"),
                "canonical_food_id": canonical.id if canonical else None,
                "distinguish_tip": relation.get("distinguish_tip"),
                "nutrition_delta": relation.get("nutrition_delta"),
                "priority": relation.get("priority", 0),
                "source": "app_seed",
            }
            if exists is None:
                db.add(FoodRelation(**values))
            else:
                for key, value in values.items():
                    setattr(exists, key, value)

        db.add(ImportBatch(
            source="app_seed",
            version="1",
            license="internal/app-seed",
            row_count=len(foods),
            checksum=checksum,
        ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    foods_data.reload()
    return {
        "total": len(foods),
        "inserted": inserted,
        "updated": updated,
        "aliases": len(aliases),
        "relations": len(relations),
        "checksum": checksum,
    }


def seed_foods_if_empty() -> dict | None:
    db = SessionLocal()
    try:
        if db.query(Food).count() > 0:
            return None
    except Exception as exc:
        logger.warning("营养库表不可用，跳过自动种子：%s", exc)
        return None
    finally:
        db.close()
    try:
        result = seed_foods_from_json(force=False)
        logger.info("营养库种子完成：%s", result)
        return result
    except Exception as exc:
        logger.warning("营养库种子失败：%s", exc)
        return None
