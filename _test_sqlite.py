import sqlite3, os

db = r"C:\Users\43120\Desktop\foodai\backend\test_write.db"
if os.path.exists(db):
    os.remove(db)

# 用最原始的方式建表
conn = sqlite3.connect(db)
cur = conn.cursor()
cur.execute("CREATE TABLE test (id INTEGER, name TEXT)")
cur.execute("INSERT INTO test VALUES (1, 'hello')")
conn.commit()
conn.close()

# 重新打开验证
conn2 = sqlite3.connect(db)
cur2 = conn2.cursor()
cur2.execute("SELECT * FROM test")
rows = cur2.fetchall()
print(f"测试表数据: {rows}")
cur2.execute("SELECT name FROM sqlite_master WHERE type='table'")
print(f"表列表: {[r[0] for r in cur2.fetchall()]}")
conn2.close()

# 检查文件
print(f"文件大小: {os.path.getsize(db)} bytes")
os.remove(db)
print("测试完成，已清理")
