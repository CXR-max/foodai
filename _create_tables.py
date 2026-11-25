import sys, os
os.chdir(r"C:\Users\43120\Desktop\foodai\backend")
sys.path.insert(0, ".")

import sqlite3
from app.db.session import engine

# 直接用 SQLAlchemy 的 metadata 生成 DDL，但先建表不建索引
from sqlalchemy import CreateTable
from app.models import *  # noqa
from app.db.session import Base

# 获取所有表，按依赖顺序
order = [
    "users", "health_profiles", "foods", "food_aliases", "food_relations",
    "food_ingredients", "food_recipes", "food_recipe_items", "import_batches",
    "diet_plans", "plan_days", "plan_chat_messages", "plan_constraint_states",
    "food_records", "recognitions", "check_ins", "weight_logs",
    "points_logs", "achievements", "user_achievements", "rewards", "redemptions",
]

tables = Base.metadata.tables

with engine.begin() as conn:
    # 第一步：只建表（不建索引）
    for name in order:
        if name in tables:
            t = tables[name]
            print(f"建表: {name}")
            # 用 CreateTable 只生成建表语句，不建索引
            ddl = CreateTable(t)
            try:
                conn.execute(ddl)
            except Exception as e:
                if "already exists" in str(e):
                    print(f"  已存在，跳过")
                else:
                    raise

    # 第二步：手动建索引
    print("\n建索引...")
    for name in order:
        if name in tables:
            t = tables[name]
            for idx in t.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    print(f"  索引 {idx.name}: {e}")

print("\n完成！")
