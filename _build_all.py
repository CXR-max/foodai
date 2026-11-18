import sys, os
os.chdir(r"C:\Users\43120\Desktop\foodai\backend")
sys.path.insert(0, ".")

from sqlalchemy.schema import CreateTable
from sqlalchemy import text
from app.db.session import Base, engine
from app.models import *  # noqa

# 按依赖顺序建所有缺失的表
order = [
    "food_aliases", "food_relations", "food_ingredients",
    "food_recipes", "food_recipe_items", "import_batches",
]

tables = Base.metadata.tables
results = []

with engine.begin() as conn:
    for name in order:
        if name in tables:
            t = tables[name]
            ddl = str(CreateTable(t).compile(engine))
            try:
                conn.execute(text(ddl))
                results.append(f"OK: {name}")
            except Exception as e:
                if "already exists" in str(e):
                    results.append(f"SKIP: {name} (已存在)")
                else:
                    results.append(f"FAIL: {name} - {e}")

# 建索引
results.append("\n=== 建索引 ===")
with engine.begin() as conn:
    for name in ["foods"] + order:
        if name in tables:
            t = tables[name]
            for idx in t.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                    results.append(f"  索引 {idx.name}: OK")
                except Exception as e:
                    if "already exists" in str(e):
                        results.append(f"  索引 {idx.name}: 已存在")
                    else:
                        results.append(f"  索引 {idx.name}: {e}")

with open(r"C:\Users\43120\Desktop\foodai\_build_result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(results))
