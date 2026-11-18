"""把开发库的食物营养全量导出为 JSON 种子（CI / 新装环境的数据源）。

背景：foods_seed.json 原来只有 108 条手工种子，CI 和没跑过导入管道的环境
只能用这个小池子——排餐钠超标被复核打回、「鸡蛋」fuzzy 错配到「鸡蛋饼」。
开发机的 SQLite 库有 1.3 万+ 条（USDA/COFiD/MEXT/TFDA 导入管道灌入），
但原始 CSV 不进仓库。本脚本把库里的条目（含别名）全量倒进种子文件，
让「没库的环境」和「有库的环境」行为一致。

用法：
    cd backend && .venv/Scripts/python.exe scripts/export_food_seed.py

注意：输出会覆盖 app/data/foods_seed.json；跑完请肉眼抽查几条再提交。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.models.food import Food, FoodAlias  # noqa: E402
from app.services.llm.foods_data import _entry_from_db  # noqa: E402

OUTPUT = ROOT / "app" / "data" / "foods_seed.json"


def main() -> None:
    session = SessionLocal()
    try:
        rows = session.query(Food).filter(Food.active.is_(True)).order_by(Food.id.asc()).all()
        by_id = {row.id: row for row in rows}
        alias_map: dict[int, list[str]] = {}
        for alias in session.query(FoodAlias).all():
            if alias.food_id in by_id:
                alias_map.setdefault(alias.food_id, []).append(alias.alias)
    finally:
        session.close()

    foods = []
    for row in rows:
        entry = _entry_from_db(row)
        entry["aliases"] = list(dict.fromkeys(alias_map.get(row.id, [])))
        # id/active 是库里的运行时字段，种子文件不需要
        entry.pop("id", None)
        entry.pop("active", None)
        foods.append(entry)

    OUTPUT.write_text(
        json.dumps(foods, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"导出 {len(foods)} 条 -> {OUTPUT}（{size_mb:.1f} MB）")


if __name__ == "__main__":
    main()
