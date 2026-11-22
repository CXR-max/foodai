import sqlite3
db = r"C:\Users\43120\Desktop\foodai\backend\foodai.db"
conn = sqlite3.connect(db)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print(f"表数: {len(tables)}")
for t in tables:
    print(f"  {t}")
conn.close()
