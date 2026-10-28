"""日本 MEXT 日本食品標準成分表（八訂）増補2023年 适配器。

文件：mext_2023_chapter2.xlsx（「表全体」工作表，第 2 章数据）
列：食品群 / 食品番号 / 索引番号 / 食品名 / 廃棄率 / エネルギー(kJ) / エネルギー(kcal) /
    水分 / たんぱく質 / 脂質 / 炭水化物 / 食物繊維総量 / ナトリウム ...
用法：
    python -m scripts.nutrition_import.mext <excel> [--commit]
许可：MEXT 公开数据，再分发前需核对利用规约（本表仅入 staging，默认不激活）。
"""
import argparse
import hashlib
import json
from pathlib import Path

from openpyxl import load_workbook

from scripts.nutrition_import.canonical import import_rows

SOURCE = "mext"
VERSION = "2023"
LICENSE = "MEXT-2023 (要確認利用規約)"

# 食品群 → 本地 category_l1
GROUP_MAP = {
    "01": "主食类", "02": "蔬菜类", "03": "调味类", "04": "豆制品", "05": "坚果类",
    "06": "蔬菜类", "07": "水果类", "08": "菌藻类", "09": "菌藻类", "10": "水产类",
    "11": "肉类", "12": "蛋类", "13": "奶类", "14": "油脂类", "15": "小吃",
    "16": "饮品", "17": "调味类", "18": "其他",
}

# 0-based 列号
COL_GROUP = 0
COL_CODE = 1
COL_NAME = 3
COL_WASTE = 4
COL_KCAL = 6
COL_PROTEIN = 9
COL_FAT = 12
COL_CARB = 20
COL_FIBER = 18
COL_SODIUM = 23


def _num(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "*", "Tr", "tr"}:
        return None
    text = text.strip("()")
    try:
        return float(text)
    except ValueError:
        return None


def build_rows(path: Path) -> list[dict]:
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["表全体"]
    rows = []
    for raw in ws.iter_rows(min_row=1, values_only=True):
        if len(raw) <= COL_SODIUM:
            continue
        code = raw[COL_CODE]
        name = raw[COL_NAME]
        if not code or not name or not str(code).strip().isdigit():
            continue
        kcal = _num(raw[COL_KCAL])
        if kcal is None:
            continue
        group = str(raw[COL_GROUP] or "").strip()[:2]
        waste = _num(raw[COL_WASTE]) or 0
        rows.append({
            "name_zh": str(name).replace("\u3000", " ").strip(),
            "category_l1": GROUP_MAP.get(group, "其他"),
            "food_type": "ingredient",
            "dish_style": "日式",
            "region": "日本",
            "cuisine": "日式",
            "state": "cooked",
            "edible_ratio": round(max(0.0, 1 - waste / 100), 3),
            "common_portion_g": 100,
            "serving_g": 100,
            "energy_kcal": kcal,
            "protein_g": _num(raw[COL_PROTEIN]),
            "fat_g": _num(raw[COL_FAT]),
            "carb_g": _num(raw[COL_CARB]),
            "fiber_g": _num(raw[COL_FIBER]),
            "sodium_mg": _num(raw[COL_SODIUM]),
            "source_id": str(code).strip(),
            "source_url": "https://www.mext.go.jp/a_menu/syokuhinseibun/mext_00001.html",
            "verified": False,
            "active": False,
        })
    wb.close()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="导入日本食品成分表 2023（默认 dry-run，写 staging）")
    parser.add_argument("excel", type=Path)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    rows = build_rows(args.excel)
    checksum = hashlib.sha256(args.excel.read_bytes()).hexdigest()
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
