import sqlite3

db = r"C:\Users\43120\Desktop\foodai\backend\foodai.db"
conn = sqlite3.connect(db)
cur = conn.cursor()

# 1. 补 health_profiles.body_fat_pct
cur.execute("PRAGMA table_info(health_profiles)")
cols = [r[1] for r in cur.fetchall()]
if "body_fat_pct" not in cols:
    cur.execute("ALTER TABLE health_profiles ADD COLUMN body_fat_pct FLOAT")
    print("OK: health_profiles.body_fat_pct")
else:
    print("SKIP: body_fat_pct 已存在")

# 2. 补 check_ins 的列
cur.execute("PRAGMA table_info(check_ins)")
ci = [r[1] for r in cur.fetchall()]
for col, typ in [("intensity","VARCHAR(20)"),("met_used","FLOAT"),("burn_detail","JSON")]:
    if col not in ci:
        cur.execute(f"ALTER TABLE check_ins ADD COLUMN {col} {typ}")
        print(f"OK: check_ins.{col}")

# 3. 建 foods 相关表
tables_sql = [
    """CREATE TABLE IF NOT EXISTS foods (
        id INTEGER NOT NULL PRIMARY KEY, source VARCHAR(50) NOT NULL, source_id VARCHAR(120) NOT NULL,
        name_zh VARCHAR(120) NOT NULL, name_en VARCHAR(240), name_norm VARCHAR(120),
        food_type VARCHAR(20), dish_style VARCHAR(30), region VARCHAR(30), cuisine VARCHAR(30),
        category_l1 VARCHAR(30), category_l2 VARCHAR(60), state VARCHAR(20),
        edible_ratio FLOAT, serving_g FLOAT, common_portion_g FLOAT,
        energy_kcal FLOAT, protein_g FLOAT, fat_g FLOAT, carb_g FLOAT, fiber_g FLOAT,
        sodium_mg FLOAT, sugar_g FLOAT, extra_nutrients JSON, ingredients JSON,
        suitable_meals JSON, taste_tags JSON, dietary_tags JSON, source_type VARCHAR(30),
        license VARCHAR(120), source_url VARCHAR(500), retrieved_at DATETIME, version VARCHAR(50),
        verified BOOLEAN, active BOOLEAN, created_at DATETIME, updated_at DATETIME,
        UNIQUE(source, source_id))""",
    """CREATE TABLE IF NOT EXISTS food_aliases (
        id INTEGER NOT NULL PRIMARY KEY, food_id INTEGER NOT NULL REFERENCES foods(id),
        alias VARCHAR(120) NOT NULL, alias_norm VARCHAR(120) NOT NULL,
        alias_type VARCHAR(30), source VARCHAR(50), created_at DATETIME,
        UNIQUE(food_id, alias_norm))""",
    """CREATE TABLE IF NOT EXISTS food_relations (
        id INTEGER NOT NULL PRIMARY KEY, group_key VARCHAR(100) NOT NULL,
        relation_type VARCHAR(20) NOT NULL, name VARCHAR(120) NOT NULL,
        name_norm VARCHAR(120) NOT NULL, canonical_name VARCHAR(120),
        canonical_food_id INTEGER REFERENCES foods(id), distinguish_tip VARCHAR(300),
        nutrition_delta JSON, priority INTEGER, source VARCHAR(50), created_at DATETIME,
        UNIQUE(group_key, relation_type, name_norm))""",
    """CREATE TABLE IF NOT EXISTS food_ingredients (
        id INTEGER NOT NULL PRIMARY KEY, food_id INTEGER NOT NULL REFERENCES foods(id),
        ingredient_id INTEGER REFERENCES foods(id), ingredient_name VARCHAR(120) NOT NULL,
        grams FLOAT, state VARCHAR(20), edible_ratio FLOAT, created_at DATETIME)""",
    """CREATE TABLE IF NOT EXISTS food_recipes (
        id INTEGER NOT NULL PRIMARY KEY, food_id INTEGER NOT NULL REFERENCES foods(id),
        version VARCHAR(50), yield_g FLOAT, cooked_yield_ratio FLOAT,
        source VARCHAR(100), created_at DATETIME)""",
    """CREATE TABLE IF NOT EXISTS food_recipe_items (
        id INTEGER NOT NULL PRIMARY KEY, recipe_id INTEGER NOT NULL REFERENCES food_recipes(id),
        ingredient_id INTEGER REFERENCES foods(id), ingredient_name VARCHAR(120) NOT NULL,
        grams FLOAT, state VARCHAR(20))""",
    """CREATE TABLE IF NOT EXISTS import_batches (
        id INTEGER NOT NULL PRIMARY KEY, source VARCHAR(50) NOT NULL, version VARCHAR(50),
        license VARCHAR(120), row_count INTEGER, checksum VARCHAR(64), imported_at DATETIME)""",
]
for sql in tables_sql:
    try:
        cur.execute(sql)
        print(f"OK: {sql.split('(')[0].strip()}")
    except Exception as e:
        print(f"SKIP: {e}")

conn.commit()

# 验证
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
all_t = [r[0] for r in cur.fetchall()]
print(f"\n总表数: {len(all_t)}")
for t in all_t:
    print(f"  {t}")

conn.close()
print("\n完成！")
