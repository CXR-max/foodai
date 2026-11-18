import argparse
import json
from pathlib import Path

from app.db.session import SessionLocal
from app.models.food import Food, FoodAlias
from app.services.food_text import normalize_name
from app.services.llm import foods_data


def import_aliases(path: Path, dry_run: bool = True) -> dict:
    items = json.loads(path.read_text(encoding="utf-8"))
    db = SessionLocal()
    inserted = skipped = missing = 0
    seen = set()
    try:
        for item in items:
            food = (
                db.query(Food)
                .filter(Food.source == item["source"], Food.source_id == item["source_id"])
                .first()
            )
            if food is None:
                missing += 1
                continue
            alias_norm = normalize_name(item["alias"])
            key = (food.id, alias_norm)
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            exists = (
                db.query(FoodAlias)
                .filter(FoodAlias.food_id == food.id, FoodAlias.alias_norm == alias_norm)
                .first()
            )
            if exists is not None:
                skipped += 1
                continue
            inserted += 1
            if not dry_run:
                db.add(FoodAlias(
                    food_id=food.id,
                    alias=item["alias"],
                    alias_norm=alias_norm,
                    alias_type=item.get("alias_type") or "regional",
                    source=item["source"],
                ))
        if not dry_run:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    if not dry_run:
        foods_data.reload()
    return {
        "total": len(items),
        "inserted": inserted,
        "skipped": skipped,
        "missing_food": missing,
        "dry_run": dry_run,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导入别名 JSON")
    parser.add_argument("file", type=Path)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    result = import_aliases(args.file, dry_run=not args.commit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
