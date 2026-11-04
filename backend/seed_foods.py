import argparse
import json

from app.services.food_seed import seed_foods_from_json


def main() -> None:
    parser = argparse.ArgumentParser(description="导入内置营养库种子数据")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的种子记录")
    args = parser.parse_args()
    result = seed_foods_from_json(force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
