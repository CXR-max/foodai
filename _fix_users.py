import sqlite3
db = r"C:\Users\43120\Desktop\foodai\backend\foodai.db"
conn = sqlite3.connect(db)
cur = conn.cursor()

# 检查 users 表列
cur.execute("PRAGMA table_info(users)")
cols = [r[1] for r in cur.fetchall()]
print(f"users 列: {cols}")

# 补缺失的列
if "equipped_achievement_id" not in cols:
    cur.execute("ALTER TABLE users ADD COLUMN equipped_achievement_id INTEGER REFERENCES achievements(id)")
    print("OK: users.equipped_achievement_id")
else:
    print("SKIP: equipped_achievement_id 已存在")

conn.commit()

# 验证
cur.execute("PRAGMA table_info(users)")
cols2 = [r[1] for r in cur.fetchall()]
print(f"更新后: {cols2}")

conn.close()
