"""把 staging 里「干净可用」的条目批量提升为 active（扩大识别可命中的闭集）。

默认 dry-run，只打印会激活什么；确认后加 --commit。

示例：
    python -m scripts.nutrition_import.promote --source tfda --type ingredient
    python -m scripts.nutrition_import.promote --source tfda --type ingredient --limit 300 --commit
"""
import argparse
import json
import re

from app.db.session import SessionLocal
from app.models.food import Food
from app.services.llm import foods_data

# 名字里带这些的，说明是「取样批次/有机/平均值」这类不适合直接匹配的条目
DIRTY_NAME = re.compile(r"[（(](19|20)\d{2}|取樣|取样|有機|有机|平均值|平均值|市售|現煮|现煮")

# 中文名才适合直接匹配；英文/日文源先不直接激活
CHINESE_SOURCES = {"tfda", "app_seed", "recipe"}

ALLOWED_TYPES = {"ingredient", "single_food", "beverage"}

# 常用度排序：名字里带这些字的先激活（蔬菜/肉/蛋/主食/常见水产），甜点零食和生僻物种靠后
COMMON_KEYWORDS = (
    "菜", "肉", "蛋", "豆", "米", "面", "麵", "粉", "油", "糖", "盐", "鹽", "酱", "醬", "醋",
    "瓜", "菇", "耳", "笋", "筍", "萝卜", "蘿蔔", "葱", "蔥", "姜", "薑", "蒜", "椒",
    "鱼", "魚", "虾", "蝦", "蟹", "贝", "貝", "奶", "果", "桃", "梨", "苹", "蘋",
)


def _common_score(food: Food) -> tuple[int, int]:
    name = food.name_zh or ""
    hit = any(word in name for word in COMMON_KEYWORDS)
    return (0 if hit else 1, len(name))


def is_clean(food: Food) -> tuple[bool, str]:
    name = (food.name_zh or "").strip()
    if not name:
        return False, "无名称"
    if DIRTY_NAME.search(name):
        return False, "名称带取样/年份/有机等后缀"
    if food.source not in CHINESE_SOURCES:
        return False, f"非中文源（{food.source}）"
    if not food.energy_kcal or food.energy_kcal <= 0:
        return False, "缺热量"
    if food.energy_kcal > 900:
        return False, "热量超 900"
    protein = food.protein_g or 0
    fat = food.fat_g or 0
    carb = food.carb_g or 0
    est = 4 * protein + 9 * fat + 4 * carb
    if abs(est - food.energy_kcal) / food.energy_kcal > 0.35:
        return False, "宏量与热量不符"
    if min(protein, fat, carb) < 0 or max(protein, fat, carb) > 105:
        return False, "宏量数值异常"
    return True, ""


def main() -> None:
    parser = argparse.ArgumentParser(description="把 staging 条目批量提升为 active")
    parser.add_argument("--source", help="来源，如 tfda")
    parser.add_argument("--type", dest="food_type", help="ingredient/single_food/beverage/dish")
    parser.add_argument("--limit", type=int, default=0, help="本次最多激活多少条（0=不限）")
    parser.add_argument("--sort", default="common", choices=["common", "id"],
                        help="common=先激活蔬菜/肉/主食等常用类，id=按入库顺序")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # 已经在活跃池里的名字（归一化后）不再重复激活，避免同一道菜两份数据
        active_names = {
            (f.name_norm or f.name_zh) for f in db.query(Food).filter(Food.active.is_(True)).all()
        }

        query = db.query(Food).filter(Food.active.is_(False))
        if args.source:
            query = query.filter(Food.source == args.source)
        if args.food_type:
            query = query.filter(Food.food_type == args.food_type)
        else:
            query = query.filter(Food.food_type.in_(ALLOWED_TYPES))
        rows = query.all()

        good, bad = [], []
        seen: set[str] = set()
        for food in rows:
            ok, reason = is_clean(food)
            key = food.name_norm or food.name_zh
            if ok and key in active_names:
                ok, reason = False, "活跃池已有同名条目"
            elif ok and key in seen:
                ok, reason = False, "本批次内重名"
            if ok:
                seen.add(key)
            (good if ok else bad).append((food, reason))

        if args.sort == "common":
            good.sort(key=lambda item: _common_score(item[0]))

        if args.limit and len(good) > args.limit:
            good = good[: args.limit]

        print(json.dumps({
            "candidates": len(rows),
            "clean": len(good),
            "rejected": len(bad),
            "sample_clean": [f.name_zh for f, _ in good[:10]],
            "reject_reasons": {},
            "dry_run": not args.commit,
        }, ensure_ascii=False, indent=2))
        if bad:
            reasons = {}
            for _, reason in bad:
                reasons[reason] = reasons.get(reason, 0) + 1
            print("拒绝原因:", json.dumps(reasons, ensure_ascii=False))

        if args.commit:
            for food, _ in good:
                food.active = True
            db.commit()
            foods_data.reload()
            total = db.query(Food).filter(Food.active.is_(True)).count()
            print(f"已激活 {len(good)} 条，当前活跃池 {total} 条")
    finally:
        db.close()


if __name__ == "__main__":
    main()
