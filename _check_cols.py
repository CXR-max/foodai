import sqlite3
db = r"C:\Users\43120\Desktop\foodai\backend\foodai.db"
conn = sqlite3.connect(db)
cur = conn.cursor()
cur.execute("PRAGMA table_info(users)")
print("users 列:")
for r in cur.fetchall():
    print(f"  {r[1]} ({r[2]})")
cur.execute("PRAGMA table_info(health_profiles)")
print("\nhealth_profiles 列:")
for r in cur.fetchall():
    print(f"  {r[1]} ({r[2]})")
cur.execute("PRAGMA table_info(check_ins)")
print("\ncheck_ins 列:")
for r in cur.fetchall():
    print(f"  {r[1]} ({r[2]})")
conn.close()
