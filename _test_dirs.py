import sqlite3, os, tempfile

# 测试写到临时目录
db = os.path.join(tempfile.gettempdir(), "test_sqlite.db")
if os.path.exists(db):
    os.remove(db)

print(f"临时目录: {db}")
conn = sqlite3.connect(db)
cur = conn.cursor()
cur.execute("CREATE TABLE test (id INTEGER, name TEXT)")
cur.execute("INSERT INTO test VALUES (1, 'hello')")
conn.commit()
cur.execute("SELECT * FROM test")
print(f"查询结果: {cur.fetchall()}")
conn.close()
print(f"文件大小: {os.path.getsize(db)} bytes")
os.remove(db)

# 再测试写到项目目录
db2 = r"C:\Users\43120\Desktop\foodai\backend\test2.db"
if os.path.exists(db2):
    os.remove(db2)
conn2 = sqlite3.connect(db2)
cur2 = conn2.cursor()
cur2.execute("CREATE TABLE test (id INTEGER)")
conn2.commit()
print(f"\n项目目录文件大小: {os.path.getsize(db2)} bytes")
conn2.close()
if os.path.exists(db2):
    os.remove(db2)
