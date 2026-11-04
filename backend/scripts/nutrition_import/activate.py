import argparse
import json

from app.db.session import SessionLocal
from app.models.food import Food
from app.services.llm import foods_data


def main() -> None:
    parser = argparse.ArgumentParser(description="启用/停用营养库条目（staging ↔ 活跃池）")
    parser.add_argument("--source", help="来源，如 tfda/cofid/app_seed")
    parser.add_argument("--food-type", help="ingredient/single_food/dish/light_meal/beverage/packaged")
    parser.add_argument("--cuisine", help="菜系")
    parser.add_argument("--name", action="append", default=[], help="精确名称，可重复")
    parser.add_argument("--id", action="append", default=[], help="source_id，可重复")
    parser.add_argument("--deactivate", action="store_true", help="改为停用；默认启用")
    parser.add_argument("--commit", action="store_true", help="真正写入；默认 dry-run")
    args = parser.parse_args()
    if not any([args.source, args.food_type, args.cuisine, args.name, args.id]):
        parser.error("至少指定一个筛选条件（--source/--food-type/--cuisine/--name/--id）")

    db = SessionLocal()
    try:
        query = db.query(Food)
        if args.source:
            query = query.filter(Food.source == args.source)
        if args.food_type:
            query = query.filter(Food.food_type == args.food_type)
        if args.cuisine:
            query = query.filter(Food.cuisine == args.cuisine)
        if args.id:
            query = query.filter(Food.source_id.in_(args.id))
        if args.name:
            query = query.filter(Food.name_zh.in_(args.name))
        foods = query.all()
        changed = [food for food in foods if bool(food.active) != (not args.deactivate)]
        if args.commit:
            for food in changed:
                food.active = not args.deactivate
            db.commit()
            foods_data.reload()
        result = {
            "matched": len(foods),
            "changed": len(changed),
            "target_active": not args.deactivate,
            "dry_run": not args.commit,
            "names": [food.name_zh for food in changed[:50]],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
