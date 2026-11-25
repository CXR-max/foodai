import sqlite3, os

db_path = r"C:\Users\43120\Desktop\foodai\backend\foodai.db"
print(f"数据库文件: {db_path}")
print(f"文件存在: {os.path.exists(db_path)}")
print(f"文件大小: {os.path.getsize(db_path)} bytes")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

# 直接建一个测试表
cur.execute("CREATE TABLE IF NOT EXISTS test_xyz (id INTEGER)")
conn.commit()

# 立即查询
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='test_xyz'")
result = cur.fetchall()
print(f"test_xyz 查询结果: {result}")

cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
all_tables = [r[0] for r in cur.fetchall()]
print(f"所有表({len(all_tables)}): {all_tables}")

# 再试建 foods
try:
    cur.execute("CREATE TABLE foods_test (id INTEGER PRIMARY KEY)")
    conn.commit()
    cur.execute("SELECT name FROM sqlite_master WHERE name='foods_test'")
    print(f"foods_test: {cur.fetchall()}")
except Exception as e:
    print(f"foods_test 失败: {e}")

conn.close()
