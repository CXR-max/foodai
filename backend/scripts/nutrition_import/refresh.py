"""活跃池清洗：去重 + 用 TFDA 官方值替换早期的估算数据。

默认只报告（dry-run），加 --commit 才写库。

    python -m scripts.nutrition_import.refresh            # 看要改什么
    python -m scripts.nutrition_import.refresh --commit   # 执行
"""
import argparse
import json

from app.db.session import SessionLocal
from app.models.food import Food
from app.services.food_text import normalize_name
from app.services.llm import foods_data

PRIORITY = {"recipe": 3, "app_seed": 2, "legacy_json": 1}
NUTRIENTS = ("energy_kcal", "protein_g", "fat_g", "carb_g", "fiber_g", "sodium_mg", "sugar_g")
REPLACE_MIN_DIFF = 0.2  # 热量差超过 20% 才用 TFDA 覆盖


def _norm(food: Food) -> str:
    return food.name_norm or normalize_name(food.name_zh or "")


def dedupe(db) -> list[dict]:
    active = db.query(Food).filter(Food.active.is_(True)).all()
    groups: dict[str, list[Food]] = {}
    for food in active:
        groups.setdefault(_norm(food), []).append(food)

    actions = []
    for key, items in groups.items():
        if len(items) < 2:
            continue
        keep = max(items, key=lambda f: (PRIORITY.get(f.source, 0), -f.id))
        for food in items:
            if food.id == keep.id:
                continue
            actions.append({
                "action": "deactivate", "id": food.id, "name": food.name_zh,
                "source": food.source, "kcal": food.energy_kcal,
                "keep_id": keep.id, "keep_source": keep.source, "keep_kcal": keep.energy_kcal,
            })
            food.active = False
    return actions


def replace_with_tfda(db) -> list[dict]:
    active = db.query(Food).filter(Food.active.is_(True)).all()
    tfda = {}
    for food in db.query(Food).filter(Food.source == "tfda").all():
        tfda.setdefault(_norm(food), []).append(food)

    actions = []
    for food in active:
        if food.source not in {"app_seed", "legacy_json"}:
            continue
        candidates = tfda.get(_norm(food)) or []
        if not candidates or not food.energy_kcal:
            continue
        better = max(candidates, key=lambda f: (f.verified or False, f.id))
        if not better.energy_kcal:
            continue
        diff = abs(food.energy_kcal - better.energy_kcal) / better.energy_kcal
        if diff < REPLACE_MIN_DIFF:
            continue
        previous = {field: getattr(food, field) for field in NUTRIENTS}
        for field in NUTRIENTS:
            setattr(food, field, getattr(better, field))
        extra = dict(food.extra_nutrients or {})
        extra["previous"] = previous
        extra["data_source"] = f"tfda:{better.name_zh}"
        food.extra_nutrients = extra
        food.verified = True
        actions.append({
            "action": "replace", "id": food.id, "name": food.name_zh,
            "old_kcal": previous["energy_kcal"], "new_kcal": food.energy_kcal,
            "diff_pct": round((previous["energy_kcal"] - food.energy_kcal) / food.energy_kcal * 100, 1),
        })
    return actions


def main() -> None:
    parser = argparse.ArgumentParser(description="活跃池去重 + TFDA 官方值替换估算")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--no-replace", action="store_true", help="只去重，不替换")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        removed = dedupe(db)
        replaced = [] if args.no_replace else replace_with_tfda(db)
        print(json.dumps({
            "dedupe": len(removed),
            "replace": len(replaced),
            "dedupe_sample": removed[:8],
            "replace_sample": replaced[:12],
            "dry_run": not args.commit,
        }, ensure_ascii=False, indent=2))
        if args.commit:
            db.commit()
            foods_data.reload()
            total = db.query(Food).filter(Food.active.is_(True)).count()
            print(f"完成：停用重复 {len(removed)} 条，替换 {len(replaced)} 条，当前活跃 {total}")
        else:
            db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
