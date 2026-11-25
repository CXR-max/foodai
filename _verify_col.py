import sqlite3, os

db_path = r"C:\Users\43120\Desktop\foodai\backend\foodai.db"
print(f"文件: {db_path}")
print(f"大小: {os.path.getsize(db_path)} bytes")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

# 直接查 health_profiles 列
cur.execute("PRAGMA table_info(health_profiles)")
cols = cur.fetchall()
print(f"\nhealth_profiles 列数: {len(cols)}")
for c in cols:
    print(f"  {c[1]} ({c[2]})")

# 直接 ALTER 并立即验证
print("\n=== 直接加列 ===")
try:
    cur.execute("ALTER TABLE health_profiles ADD COLUMN body_fat_pct FLOAT")
    print("ALTER 执行成功")
except Exception as e:
    print(f"ALTER 错误: {e}")

conn.commit()
print(f"commit 完成")

# 立即查
cur.execute("PRAGMA table_info(health_profiles)")
cols2 = [r[1] for r in cur.fetchall()]
print(f"加完后列: {cols2}")
print(f"body_fat_pct 在列中: {'body_fat_pct' in cols2}")

conn.close()
