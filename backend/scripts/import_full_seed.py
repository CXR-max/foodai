"""把版本库自带的「全量营养库」还原进 SQLite（离线 / 幂等 / 不联网）。

背景
----
主分支 ``seed_all.py`` 走 ``food_seed.seed_foods_from_json``，它读的是
``name_zh`` / ``nutrition`` 格式的「种子输入」；但仓库里的
``app/data/foods_seed.json`` 其实是 ``scripts/export_food_seed.py`` 从开发库
导出的**活跃池快照**，字段是 ``name`` / ``per_100g`` / 内联 ``aliases``。
两者格式对不上，直接跑 seed_all 会 ``KeyError: 'name_zh'``。

本脚本按导出格式反向灌库，等价还原主分支随仓库发布的全部活跃数据：

* ``foods_seed.json`` 1,847 条活跃池（含 TFDA / 菜谱 / 内置种子 / legacy）
  + 内联 3,988 条别名
* ``food_staples.json`` 80 条常用食材（菜谱法算营养的原子数据）
* ``food_aliases.json`` 14 条 / ``food_relations.json`` 57 条关系
* 200 道菜谱（``import_recipes``，按食材重算营养）
* 默认激活家常 / 川 / 粤 / 鲁 / 苏菜系

注意：README 里 13,423 条中的 11,576 条 staging 来自 USDA/TFDA/CoFID/MEXT
外部原始数据，放在 ``data_sources/``（已被 gitignore，不随仓库发布），
本脚本不联网下载，只还原仓库自带的完整活跃池。

用法（在 backend 目录）：
    .venv\\Scripts\\python.exe scripts\\import_full_seed.py
可重复执行：先清空营养库相关表再重建，业务数据（food_records / recognitions
/check_ins 等）不受影响 —— 它们不引用 foods.id。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.food import (  # noqa: E402
    Food, FoodAlias, FoodIngredient, FoodRecipe, FoodRecipeItem,
    FoodRelation, ImportBatch,
)
from app.services.food_text import normalize_name  # noqa: E402
from app.services.llm import foods_data  # noqa: E402
from scripts.nutrition_import.recipes import import_recipes  # noqa: E402

DATA = ROOT / "app" / "data"
SEED_FILE = DATA / "foods_seed.json"
STAPLE_FILE = DATA / "food_staples.json"
ALIAS_FILE = DATA / "food_aliases.json"
RELATION_FILE = DATA / "food_relations.json"
RECIPE_FILES = ("recipes_50.json", "recipes_extra_a.json", "recipes_extra_b.json")
ACTIVE_CUISINES = ("家常菜", "川", "粤", "鲁", "苏")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def to_values(item: dict) -> dict:
    """兼容两种格式 → Food 模型字段。

    * 导出格式：``name`` / ``per_100g`` / ``category``（foods_seed.json）
    * 种子格式：``name_zh`` / ``nutrition`` / ``category_l1``（food_staples.json）
    """
    name = item.get("name_zh") or item.get("name")
    nut = item.get("per_100g") or item.get("nutrition") or {}
    portion = float(item.get("common_portion_g") or item.get("serving_g") or 100)
    return {
        "source": item["source"],
        "source_id": str(item["source_id"]),
        "name_zh": name,
        "name_en": item.get("name_en"),
        "name_norm": item.get("name_norm") or normalize_name(name),
        "food_type": item.get("food_type") or "dish",
        "dish_style": item.get("dish_style") or "家常菜",
        "region": item.get("region") or "通用",
        "cuisine": item.get("cuisine") or "家常菜",
        "category_l1": item.get("category_l1") or item.get("category") or "其他",
        "category_l2": item.get("category_l2"),
        "state": item.get("state") or "cooked",
        "edible_ratio": float(item.get("edible_ratio") or 1.0),
        "serving_g": item.get("serving_g") or portion,
        "common_portion_g": portion,
        "energy_kcal": nut.get("calories") or nut.get("energy_kcal"),
        "protein_g": nut.get("protein_g"),
        "fat_g": nut.get("fat_g"),
        "carb_g": nut.get("carb_g"),
        "fiber_g": nut.get("fiber_g"),
        "sodium_mg": nut.get("sodium_mg"),
        "sugar_g": nut.get("sugar_g"),
        "ingredients": item.get("ingredients") or [],
        "suitable_meals": item.get("suitable_meals") or [],
        "taste_tags": item.get("taste_tags") or [],
        "dietary_tags": item.get("dietary_tags") or [],
        "source_type": item.get("source_type") or "seed_estimate",
        "license": item.get("license"),
        "source_url": item.get("source_url"),
        "version": str(item.get("version") or "1"),
        "verified": bool(item.get("verified", False)),
        # 导出时 pop 掉了 active；活跃池快照里的每一条都应是 active
        "active": bool(item.get("active", True)),
    }


def clear_library(db) -> None:
    """按外键依赖顺序清空营养库表（不动任何业务表）。"""
    for model in (FoodRecipeItem, FoodRecipe, FoodIngredient,
                  FoodRelation, FoodAlias, ImportBatch, Food):
        db.query(model).delete(synchronize_session=False)
    db.flush()


def upsert_foods(db, items: list[dict]) -> tuple[int, int, dict]:
    """按 (source, source_id) 去重灌入，返回 (新增, 覆盖, 名称→Food 映射)。"""
    by_key: dict[tuple[str, str], dict] = {}
    for item in items:
        v = to_values(item)
        by_key[(v["source"], v["source_id"])] = v

    name_map: dict[str, Food] = {}
    inserted = 0
    for v in by_key.values():
        food = Food(**v)
        db.add(food)
        db.flush()
        name_map[v["name_zh"]] = food
        inserted += 1
    return inserted, 0, name_map


def main() -> None:
    seed = read_json(SEED_FILE)
    staples = read_json(STAPLE_FILE) if STAPLE_FILE.exists() else []
    aliases_file = read_json(ALIAS_FILE) if ALIAS_FILE.exists() else []
    relations_file = read_json(RELATION_FILE) if RELATION_FILE.exists() else []
    print(f"[输入] 活跃池 {len(seed)} / 常用食材 {len(staples)} / "
          f"别名 {len(aliases_file)} / 关系 {len(relations_file)}")

    db = SessionLocal()
    try:
        clear_library(db)
        _, _, name_map = upsert_foods(db, seed + staples)

        # 1) foods_seed.json 内联别名（3,988 条）
        alias_count = 0
        seen: set[tuple[int, str]] = set()
        for item in seed:
            food = name_map.get(item.get("name_zh") or item.get("name"))
            if food is None:
                continue
            for alias in item.get("aliases") or []:
                norm = normalize_name(alias)
                if not norm or (food.id, norm) in seen:
                    continue
                seen.add((food.id, norm))
                db.add(FoodAlias(food_id=food.id, alias=alias, alias_norm=norm,
                                 alias_type="regional", source="export_seed"))
                alias_count += 1

        # 2) 独立别名文件（按名称回挂）
        for alias in aliases_file:
            target = name_map.get(alias.get("name"))
            if target is None:
                continue
            norm = normalize_name(alias.get("alias", ""))
            if not norm or (target.id, norm) in seen:
                continue
            seen.add((target.id, norm))
            db.add(FoodAlias(food_id=target.id, alias=alias["alias"],
                             alias_norm=norm,
                             alias_type=alias.get("alias_type") or "regional",
                             source="app_seed"))
            alias_count += 1

        # 3) 易混淆 / 同义关系
        relation_count = 0
        for rel in relations_file:
            canonical = name_map.get(rel.get("canonical_name") or "")
            db.add(FoodRelation(
                group_key=rel["group_key"],
                relation_type=rel["relation_type"],
                name=rel["name"],
                name_norm=normalize_name(rel["name"]),
                canonical_name=rel.get("canonical_name"),
                canonical_food_id=canonical.id if canonical else None,
                distinguish_tip=rel.get("distinguish_tip"),
                nutrition_delta=rel.get("nutrition_delta"),
                priority=rel.get("priority", 0),
                source="app_seed",
            ))
            relation_count += 1

        db.add(ImportBatch(source="full_seed_restore", version="1",
                           license="internal/repo-seed", row_count=len(seed) + len(staples)))
        db.commit()
        total_seed = db.query(Food).count()
        print(f"[灌入] 食物 {total_seed} 条 / 别名 {alias_count} 条 / 关系 {relation_count} 条")

        # 4) 200 道菜谱（依赖上面的食材库重算营养）。
        #    foods_seed.json 是「活跃池导出快照」，里面 recipe 条目本就 active；
        #    这里用 activate=True 保持快照状态，不能让 force 覆盖把它们关掉。
        recipe_total = recipe_skipped = 0
        for name in RECIPE_FILES:
            path = DATA / name
            result = import_recipes(path, activate=True, force=True, dry_run=False)
            recipe_total += result.get("total", 0)
            recipe_skipped += sum(1 for r in result.get("results", []) if r.get("skipped"))
            print(f"       {name}: {result.get('total', 0)} 道")

        # 5) 兜底：确保全部菜谱条目处于活跃池（与导出快照口径一致）
        activated = db.query(Food).filter(
            Food.source == "recipe", Food.active.is_(False)).update(
            {Food.active: True}, synchronize_session=False)
        db.commit()

        total = db.query(Food).count()
        active = db.query(Food).filter(Food.active.is_(True)).count()
        aliases = db.query(FoodAlias).count()
        relations = db.query(FoodRelation).count()
        recipes = db.query(FoodRecipe).count()
        sources = dict(Counter(r[0] for r in db.query(Food.source).all()))
        print(f"[完成] 食物 总计 {total} / 活跃 {active} / 别名 {aliases} / "
              f"关系 {relations} / 菜谱明细 {recipes}")
        print(f"       来源分布 {sources}")
        print(f"       菜谱 {recipe_total} 道，缺食材跳过 {recipe_skipped} 道，兜底激活 {activated} 道")

        foods_data.reload()
        print("[缓存] foods_data 已 reload")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
