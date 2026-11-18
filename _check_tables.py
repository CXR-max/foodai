import sqlite3, os, sys
os.chdir(r"C:\Users\43120\Desktop\foodai\backend")
conn = sqlite3.connect("foodai.db")
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
with open(r"C:\Users\43120\Desktop\foodai\_tables.txt", "w", encoding="utf-8") as f:
    f.write(f"共{len(tables)}张表:\n")
    for t in tables:
        f.write(f"  {t}\n")
conn.close()
