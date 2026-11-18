import argparse
import csv
import json
import re
from pathlib import Path

NUTRIENT_MAP = {
    "熱量": "energy_kcal",
    "粗蛋白": "protein_g",
    "粗脂肪": "fat_g",
    "總碳水化合物": "carb_g",
    "膳食纖維": "fiber_g",
    "鈉": "sodium_mg",
    "糖質總量": "sugar_g",
}

CATEGORY_MAP = {
    "魚貝類": ("基础食材", "基础食材", "ingredient"),
    "肉類": ("基础食材", "基础食材", "ingredient"),
    "蛋類": ("基础食材", "基础食材", "ingredient"),
    "豆類": ("基础食材", "基础食材", "ingredient"),
    "乳品類": ("基础食材", "基础食材", "ingredient"),
    "蔬菜類": ("基础食材", "基础食材", "ingredient"),
    "菇類": ("基础食材", "基础食材", "ingredient"),
    "藻類": ("基础食材", "基础食材", "ingredient"),
    "堅果及種子類": ("基础食材", "基础食材", "ingredient"),
    "穀物類": ("基础食材", "基础食材", "ingredient"),
    "澱粉類": ("基础食材", "基础食材", "ingredient"),
    "油脂類": ("基础食材", "基础食材", "ingredient"),
    "糖類": ("基础食材", "基础食材", "ingredient"),
    "調味料及香辛料類": ("基础食材", "基础食材", "ingredient"),
    "水果類": ("水果", "单品", "single_food"),
    "加工調理食品及其他類": ("家常菜", "家常菜", "dish"),
    "糕餅點心類": ("小吃", "小吃", "dish"),
    "飲料類": ("饮品", "饮品", "beverage"),
}

FIELDS = [
    "name_zh", "name_en", "category_l1", "category_l2", "food_type", "dish_style",
    "region", "cuisine", "state", "edible_ratio", "serving_g", "common_portion_g",
    "energy_kcal", "protein_g", "fat_g", "carb_g", "fiber_g", "sodium_mg", "sugar_g",
    "ingredients", "taste_tags", "dietary_tags", "suitable_meals",
    "source_id", "source_url", "verified", "active",
]


def number(value):
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def clean(value):
    return (value or "").strip()


def state_from(description: str, category: str):
    text = description or ""
    if "熟" in text or "調理" in text:
        return "cooked"
    if "加工" in text:
        return "processed"
    if "生" in text:
        return "raw"
    if category in {"加工調理食品及其他類", "糕餅點心類", "飲料類"}:
        return "processed"
    return "raw"


def split_aliases(value: str, name: str) -> list[str]:
    aliases = []
    for part in re.split(r"[,，、;；]", value or ""):
        alias = part.strip()
        if alias and alias != name and alias not in aliases:
            aliases.append(alias)
    return aliases


def build(csv_path: Path, out_path: Path, alias_path: Path, version: str) -> None:
    groups: dict[str, dict] = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = clean(row.get("整合編號"))
            if not code:
                continue
            item = groups.get(code)
            if item is None:
                category = clean(row.get("食品分類"))
                category_l1, dish_style, food_type = CATEGORY_MAP.get(
                    category, ("基础食材", "基础食材", "ingredient")
                )
                description = clean(row.get("內容物描述"))
                waste = number(row.get("廢棄率"))
                unit_weight = number(row.get("每單位重"))
                edible = 1 - waste / 100 if waste is not None else 1
                edible = min(max(edible, 0), 1)
                prepared = category in {"加工調理食品及其他類", "糕餅點心類", "飲料類"}
                serving = unit_weight if prepared and unit_weight and 10 <= unit_weight <= 1000 else 100
                item = {
                    "name_zh": clean(row.get("樣品名稱")),
                    "name_en": clean(row.get("樣品英文名稱")),
                    "category_l1": category_l1,
                    "category_l2": category,
                    "food_type": food_type,
                    "dish_style": dish_style,
                    "region": "台湾",
                    "cuisine": "台式" if category in {"加工調理食品及其他類", "糕餅點心類", "飲料類"} else "通用",
                    "state": state_from(description, category),
                    "edible_ratio": edible,
                    "serving_g": serving,
                    "common_portion_g": serving,
                    "ingredients": "",
                    "taste_tags": "",
                    "dietary_tags": "",
                    "suitable_meals": "",
                    "source_id": code,
                    "verified": "true",
                    "active": "false",
                    "aliases": split_aliases(clean(row.get("俗名")), clean(row.get("樣品名稱"))),
                    "source_url": "https://data.gov.tw/dataset/8543",
                }
                for field in NUTRIENT_MAP.values():
                    item[field] = None
                groups[code] = item
            field = NUTRIENT_MAP.get(clean(row.get("分析項")))
            if field and item.get(field) is None:
                item[field] = number(row.get("每100克含量"))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for item in groups.values():
            writer.writerow({key: item.get(key) for key in FIELDS})

    aliases = []
    for item in groups.values():
        for alias in item.pop("aliases", []):
            aliases.append({
                "source": "tfda",
                "source_id": item["source_id"],
                "alias": alias,
                "alias_type": "regional",
            })
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    alias_path.write_text(json.dumps(aliases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(groups)} foods to {out_path}")
    print(f"wrote {len(aliases)} aliases to {alias_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="把 TFDA 长表转换成规范营养表")
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--aliases-out", type=Path, required=True)
    parser.add_argument("--version", default="2026-10")
    args = parser.parse_args()
    build(args.csv, args.out, args.aliases_out, args.version)


if __name__ == "__main__":
    main()
