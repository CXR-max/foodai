"""一键建库：迁移 → 种子（菜品/食材/别名/关系）→ 200 道菜谱 → 默认激活。

新克隆仓库后跑这一条就够了：
    cd backend
    .\\.venv\\Scripts\\python.exe seed_all.py

可选参数：
    --no-migrate   跳过 alembic upgrade head
    --no-activate  菜谱只进 staging，不激活（默认激活家常/川/粤/鲁/苏 121 道）
"""
import argparse
import subprocess
import sys
from pathlib import Path

from app.db.session import SessionLocal
from app.models.food import Food
from app.services.food_seed import seed_foods_from_json
from app.services.llm import foods_data
from scripts.nutrition_import.recipes import import_recipes

BASE = Path(__file__).resolve().parent
RECIPE_FILES = (
    "app/data/recipes_50.json",
    "app/data/recipes_extra_a.json",
    "app/data/recipes_extra_b.json",
)
DEFAULT_ACTIVE_CUISINES = ("家常菜", "川", "粤", "鲁", "苏")


def migrate() -> None:
    print("[1/4] alembic upgrade head ...")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BASE, check=True)


def seed() -> None:
    print("[2/4] 种子数据（菜品 + 食材 + 别名 + 易混淆关系）...")
    print("     ", seed_foods_from_json(force=True))


def recipes() -> None:
    print("[3/4] 菜谱法菜品 ...")
    for name in RECIPE_FILES:
        result = import_recipes(BASE / name, activate=False, force=True, dry_run=False)
        print(f"      {name}: {result['total']} 道")


def activate() -> None:
    print("[4/4] 激活默认菜系 ...")
    db = SessionLocal()
    try:
        changed = 0
        for cuisine in DEFAULT_ACTIVE_CUISINES:
            rows = (db.query(Food)
                    .filter(Food.source == "recipe", Food.cuisine == cuisine, Food.active.is_(False))
                    .all())
            for row in rows:
                row.active = True
                changed += 1
        db.commit()
        foods_data.reload()
        active = db.query(Food).filter(Food.active.is_(True)).count()
        total = db.query(Food).count()
        print(f"      激活 {changed} 道 | 活跃 {active} / 总计 {total}")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="一键建库（迁移 + 种子 + 菜谱 + 激活）")
    parser.add_argument("--no-migrate", action="store_true", help="跳过 alembic 迁移")
    parser.add_argument("--no-activate", action="store_true", help="菜谱只进 staging，不激活")
    args = parser.parse_args()

    if not args.no_migrate:
        migrate()
    seed()
    recipes()
    if args.no_activate:
        print("[4/4] 跳过激活（--no-activate）")
    else:
        activate()


if __name__ == "__main__":
    main()
