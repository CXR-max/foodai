import sqlite3
db = r"C:\Users\43120\Desktop\foodai\backend\foodai.db"
conn = sqlite3.connect(db)
cur = conn.cursor()
cur.execute("SELECT * FROM alembic_version")
print("alembic_version:", cur.fetchall())
cur.execute("PRAGMA table_info(health_profiles)")
cols = [r[1] for r in cur.fetchall()]
print(f"health_profiles has body_fat_pct: {'body_fat_pct' in cols}")
cur.execute("PRAGMA table_info(check_ins)")
ci = [r[1] for r in cur.fetchall()]
print(f"check_ins has intensity: {'intensity' in ci}")
conn.close()
