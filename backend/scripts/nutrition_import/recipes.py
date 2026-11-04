import argparse
import json
from pathlib import Path

from app.db.session import SessionLocal
from app.models.food import Food, FoodAlias, FoodRecipe, FoodRecipeItem, ImportBatch
from app.services.food_text import infer_region, normalize_name
from app.services.llm import foods_data

NUTRIENTS = ("energy_kcal", "protein_g", "fat_g", "carb_g", "fiber_g", "sodium_mg", "sugar_g")


def find_ingredient(db, name: str) -> Food | None:
    norm = normalize_name(name)
    direct = (
        db.query(Food)
        .filter(Food.name_norm == norm)
        .order_by(Food.active.desc(), Food.verified.desc(), Food.id.asc())
        .first()
    )
    if direct is not None:
        return direct
    alias = (
        db.query(Food)
        .join(FoodAlias, FoodAlias.food_id == Food.id)
        .filter(FoodAlias.alias_norm == norm)
        .order_by(Food.active.desc(), Food.verified.desc(), Food.id.asc())
        .first()
    )
    return alias


def calculate(recipe: dict, db) -> tuple[dict, list[str], list[dict]]:
    items = []
    missing = []
    totals = {field: 0.0 for field in NUTRIENTS}
    total_grams = 0.0
    for item in recipe.get("ingredients") or []:
        name = str(item.get("name") or "").strip()
        grams = float(item.get("grams") or 0)
        if not name or grams <= 0:
            missing.append(name or "空食材")
            continue
        food = find_ingredient(db, name)
        if food is None:
            missing.append(name)
            continue
        factor = grams / 100
        for field in NUTRIENTS:
            value = getattr(food, field) or 0
            totals[field] += value * factor
        total_grams += grams
        items.append({
            "ingredient_id": food.id,
            "ingredient_name": food.name_zh,
            "grams": grams,
            "state": food.state,
        })
    yield_g = float(recipe.get("yield_g") or total_grams or 0)
    if yield_g <= 0:
        raise ValueError("yield_g 必须大于 0")
    factor = 100 / yield_g
    per_100g = {field: round(value * factor, 2) for field, value in totals.items()}
    return per_100g, missing, items


def build_values(recipe: dict, per_100g: dict, items: list[dict], activate: bool) -> dict:
    name = str(recipe["name"]).strip()
    portion = float(recipe.get("portion_g") or recipe.get("yield_g") or sum(i["grams"] for i in items))
    cuisine = str(recipe.get("cuisine") or "家常菜").strip()
    category = str(recipe.get("category_l1") or "荤菜").strip()
    return {
        "source": "recipe",
        "source_id": name,
        "name_zh": name,
        "name_en": recipe.get("name_en"),
        "name_norm": normalize_name(name),
        "food_type": recipe.get("food_type") or "dish",
        "dish_style": recipe.get("dish_style") or "家常菜",
        "region": recipe.get("region") or infer_region(cuisine),
        "cuisine": cuisine,
        "category_l1": category,
        "category_l2": recipe.get("category_l2"),
        "state": "cooked",
        "edible_ratio": float(recipe.get("edible_ratio") or 1.0),
        "serving_g": portion,
        "common_portion_g": portion,
        "energy_kcal": per_100g["energy_kcal"],
        "protein_g": per_100g["protein_g"],
        "fat_g": per_100g["fat_g"],
        "carb_g": per_100g["carb_g"],
        "fiber_g": per_100g["fiber_g"],
        "sodium_mg": per_100g["sodium_mg"],
        "sugar_g": per_100g["sugar_g"],
        "ingredients": [item["ingredient_name"] for item in items],
        "suitable_meals": recipe.get("suitable_meals") or [],
        "taste_tags": recipe.get("taste_tags") or [],
        "dietary_tags": recipe.get("dietary_tags") or [],
        "source_type": "recipe_estimate",
        "license": "internal-recipe",
        "source_url": recipe.get("source_url"),
        "version": str(recipe.get("version") or "1"),
        "verified": bool(recipe.get("verified", False)),
        "active": activate,
    }


def import_recipes(path: Path, activate: bool, force: bool, dry_run: bool) -> dict:
    recipes = json.loads(path.read_text(encoding="utf-8"))
    db = SessionLocal()
    results = []
    try:
        for recipe in recipes:
            try:
                per_100g, missing, items = calculate(recipe, db)
            except Exception as exc:
                results.append({"name": recipe.get("name"), "error": str(exc)})
                continue
            values = build_values(recipe, per_100g, items, activate)
            existing = (
                db.query(Food)
                .filter(Food.source == "recipe", Food.source_id == values["source_id"])
                .first()
            )
            result = {
                "name": values["name_zh"],
                "per_100g": per_100g,
                "yield_g": float(recipe.get("yield_g") or sum(i["grams"] for i in items)),
                "missing_ingredients": missing,
                "existing": existing is not None,
                "dry_run": dry_run,
            }
            results.append(result)
            if dry_run or (existing is not None and not force):
                continue
            if missing:
                result["skipped"] = True
                continue
            if existing is None:
                food = Food(**values)
                db.add(food)
                db.flush()
            else:
                food = existing
                for key, value in values.items():
                    setattr(food, key, value)
                db.query(FoodRecipeItem).filter(
                    FoodRecipeItem.recipe_id.in_(
                        db.query(FoodRecipe.id).filter(FoodRecipe.food_id == food.id)
                    )
                ).delete(synchronize_session=False)
                db.query(FoodRecipe).filter(FoodRecipe.food_id == food.id).delete(synchronize_session=False)
                db.flush()
            recipe_row = FoodRecipe(
                food_id=food.id,
                version=values["version"],
                yield_g=result["yield_g"],
                cooked_yield_ratio=result["yield_g"] / sum(i["grams"] for i in items),
                source="internal-recipe",
            )
            db.add(recipe_row)
            db.flush()
            for item in items:
                db.add(FoodRecipeItem(
                    recipe_id=recipe_row.id,
                    ingredient_id=item["ingredient_id"],
                    ingredient_name=item["ingredient_name"],
                    grams=item["grams"],
                    state=item["state"],
                ))
        if not dry_run:
            db.add(ImportBatch(
                source="recipe",
                version="1",
                license="internal-recipe",
                row_count=len(recipes),
            ))
            db.commit()
            foods_data.reload()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {"total": len(recipes), "results": results, "dry_run": dry_run}


def main() -> None:
    parser = argparse.ArgumentParser(description="用食材克重生成菜品营养并写入 food_recipes")
    parser.add_argument("file", type=Path)
    parser.add_argument("--activate", action="store_true", help="生成后直接进入活跃池")
    parser.add_argument("--force", action="store_true", help="覆盖同名 recipe 记录")
    parser.add_argument("--commit", action="store_true", help="真正写入；默认 dry-run")
    args = parser.parse_args()
    result = import_recipes(args.file, activate=args.activate, force=args.force, dry_run=not args.commit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
