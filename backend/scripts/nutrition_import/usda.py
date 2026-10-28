"""USDA FNDDS 2021-2023 适配器：把官方 Excel 转成营养库 staging 行。

数据来源：USDA ARS Food Surveys Research Group, FNDDS 2021-2023（公有领域，需署名）。
文件：FNDDS Nutrient Values.xlsx（每 100g 营养，宽表）+ Portions and Weights.xlsx（份量克重）
用法：
    python -m scripts.nutrition_import.usda <目录> [--commit]
"""
import argparse
import hashlib
import json
from pathlib import Path

from openpyxl import load_workbook

from scripts.nutrition_import.canonical import import_rows

SOURCE = "usda_fndds"
VERSION = "2021-2023"
LICENSE = "public-domain (USDA FNDDS 2021-2023)"

# WWEIA 一级类别号 → 本地 category_l1
CATEGORY_MAP = {
    "1": "奶类", "2": "肉类", "3": "混合菜", "4": "主食类", "5": "小吃",
    "6": "水果类", "7": "蔬菜类", "8": "饮品", "9": "油脂类",
}


def _num(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "*", "(0)", "0"}:
        return 0.0 if text in {"(0)", "0"} else None
    text = text.strip("()")
    try:
        return float(text)
    except ValueError:
        return None


def _category(code) -> str:
    text = str(code or "")
    return CATEGORY_MAP.get(text[:1], "其他")


def _load_nutrients(path: Path) -> dict[str, dict]:
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    header = next(ws.iter_rows(min_row=2, max_row=2, values_only=True))
    index = {str(name).split("\n")[0].strip(): i for i, name in enumerate(header) if name}
    col = {
        "code": index["Food code"],
        "name": index["Main food description"],
        "category": index["WWEIA Category number"],
        "energy": index["Energy (kcal)"],
        "protein": index["Protein (g)"],
        "carb": index["Carbohydrate (g)"],
        "sugar": index["Sugars, total\t(g)"] if "Sugars, total\t(g)" in index else index.get("Sugars, total"),
        "fiber": index["Fiber, total dietary (g)"],
        "fat": index["Total Fat (g)"],
        "sodium": index["Sodium (mg)"],
    }
    foods = {}
    for row in ws.iter_rows(min_row=3, values_only=True):
        code = row[col["code"]]
        if not code:
            continue
        foods[str(code).strip()] = {
            "name": str(row[col["name"]] or "").strip(),
            "category": row[col["category"]],
            "energy_kcal": _num(row[col["energy"]]),
            "protein_g": _num(row[col["protein"]]),
            "carb_g": _num(row[col["carb"]]),
            "sugar_g": _num(row[col["sugar"]]) if col.get("sugar") is not None else None,
            "fiber_g": _num(row[col["fiber"]]),
            "fat_g": _num(row[col["fat"]]),
            "sodium_mg": _num(row[col["sodium"]]),
        }
    wb.close()
    return foods


def _load_portions(path: Path) -> dict[str, float]:
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    header = next(ws.iter_rows(min_row=2, max_row=2, values_only=True))
    index = {str(name).split("\n")[0].strip(): i for i, name in enumerate(header) if name}
    code_col = index["Food code"]
    weight_col = index["Portion weight"]
    portions: dict[str, float] = {}
    for row in ws.iter_rows(min_row=3, values_only=True):
        code = row[code_col]
        weight = _num(row[weight_col])
        if not code or not weight or weight <= 0:
            continue
        key = str(code).strip()
        # 每种食物取第一份（官方表里第一份通常是标准份量）
        portions.setdefault(key, round(weight))
    wb.close()
    return portions


def build_rows(directory: Path) -> list[dict]:
    nutrients = _load_nutrients(directory / "FNDDS Nutrient Values.xlsx")
    portions = _load_portions(directory / "Portions and Weights.xlsx")
    rows = []
    for code, food in nutrients.items():
        if not food["name"] or food["energy_kcal"] is None:
            continue
        portion = portions.get(code) or 100
        rows.append({
            "name_zh": food["name"],
            "name_en": food["name"],
            "category_l1": _category(food["category"]),
            "food_type": "ingredient",
            "dish_style": "西式",
            "region": "通用",
            "cuisine": "西式",
            "state": "cooked",
            "serving_g": portion,
            "common_portion_g": portion,
            "energy_kcal": food["energy_kcal"],
            "protein_g": food["protein_g"],
            "fat_g": food["fat_g"],
            "carb_g": food["carb_g"],
            "fiber_g": food["fiber_g"],
            "sodium_mg": food["sodium_mg"],
            "sugar_g": food["sugar_g"],
            "source_id": code,
            "source_url": "https://www.ars.usda.gov/ne/bhnrc/fsrg",
            "verified": False,
            "active": False,
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="导入 USDA FNDDS 2021-2023（默认 dry-run，写 staging）")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--commit", action="store_true", help="写入数据库；不加只做 dry-run")
    parser.add_argument("--force", action="store_true", help="覆盖同 source_id 已有记录")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    rows = build_rows(args.directory)
    checksum = hashlib.sha256(
        (args.directory / "FNDDS Nutrient Values.xlsx").read_bytes()
    ).hexdigest()
    result = import_rows(
        rows, source=SOURCE, version=VERSION, license=LICENSE,
        source_type="official", dry_run=not args.commit, force=args.force, checksum=checksum,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
