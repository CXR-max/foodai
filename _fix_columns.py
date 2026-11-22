import sqlite3

db_path = r"C:\Users\43120\Desktop\foodai\backend\foodai.db"
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# 检查 health_profiles 现有列
cur.execute("PRAGMA table_info(health_profiles)")
cols = [r[1] for r in cur.fetchall()]
print(f"health_profiles 现有列: {cols}")

# 补缺失的列
alters = []
if "body_fat_pct" not in cols:
    alters.append("ALTER TABLE health_profiles ADD COLUMN body_fat_pct FLOAT")

# check_ins 表也补
cur.execute("PRAGMA table_info(check_ins)")
ci_cols = [r[1] for r in cur.fetchall()]
print(f"check_ins 现有列: {ci_cols}")
if "intensity" not in ci_cols:
    alters.append("ALTER TABLE check_ins ADD COLUMN intensity VARCHAR(20)")
if "met_used" not in ci_cols:
    alters.append("ALTER TABLE check_ins ADD COLUMN met_used FLOAT")
if "burn_detail" not in ci_cols:
    alters.append("ALTER TABLE check_ins ADD COLUMN burn_detail JSON")

for sql in alters:
    try:
        cur.execute(sql)
        print(f"OK: {sql[:60]}")
    except Exception as e:
        print(f"SKIP: {e}")

conn.commit()

# 验证
cur.execute("PRAGMA table_info(health_profiles)")
cols2 = [r[1] for r in cur.fetchall()]
print(f"\nhealth_profiles 更新后: {cols2}")
cur.execute("PRAGMA table_info(check_ins)")
ci_cols2 = [r[1] for r in cur.fetchall()]
print(f"check_ins 更新后: {ci_cols2}")

conn.close()
print("\n完成！")
