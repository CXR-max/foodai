import sys, os
os.chdir(r"C:\Users\43120\Desktop\foodai\backend")
sys.path.insert(0, ".")

from app.db.session import engine
from sqlalchemy import text

with engine.connect() as conn:
    # 加 health_profiles.body_fat_pct
    try:
        conn.execute(text("ALTER TABLE health_profiles ADD COLUMN body_fat_pct FLOAT"))
        conn.commit()
        print("OK: health_profiles.body_fat_pct")
    except Exception as e:
        print(f"SKIP body_fat_pct: {e}")

    # 加 check_ins 列
    for col, typ in [("intensity", "VARCHAR(10)"), ("met_used", "FLOAT"), ("burn_detail", "JSON")]:
        try:
            conn.execute(text(f"ALTER TABLE check_ins ADD COLUMN {col} {typ}"))
            conn.commit()
            print(f"OK: check_ins.{col}")
        except Exception as e:
            print(f"SKIP {col}: {e}")

# 验证
with engine.connect() as conn:
    result = conn.execute(text("PRAGMA table_info(health_profiles)"))
    cols = [r[1] for r in result]
    print(f"\nhealth_profiles 有 body_fat_pct: {'body_fat_pct' in cols}")
    result2 = conn.execute(text("PRAGMA table_info(check_ins)"))
    ci = [r[1] for r in result2]
    print(f"check_ins 有 intensity: {'intensity' in ci}")
