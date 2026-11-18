import sqlite3, os, traceback

db = r"C:\Users\43120\Desktop\foodai\backend\test_write.db"
if os.path.exists(db):
    os.remove(db)

print(f"数据库路径: {db}")
print(f"目录存在: {os.path.exists(os.path.dirname(db))}")

conn = sqlite3.connect(db)
cur = conn.cursor()

try:
    cur.execute("CREATE TABLE test (id INTEGER, name TEXT)")
    print("CREATE TABLE 执行成功")
except Exception as e:
    print(f"CREATE TABLE 失败: {e}")
    traceback.print_exc()

# 立即查表
try:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    print(f"sqlite_master 表: {cur.fetchall()}")
except Exception as e:
    print(f"查询失败: {e}")

try:
    cur.execute("INSERT INTO test VALUES (1, 'hello')")
    print("INSERT 成功")
except Exception as e:
    print(f"INSERT 失败: {e}")

conn.commit()
conn.close()

# 检查文件
print(f"\n文件存在: {os.path.exists(db)}")
if os.path.exists(db):
    print(f"文件大小: {os.path.getsize(db)}")
