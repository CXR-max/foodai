import sys, os, traceback
os.chdir(r"C:\Users\43120\Desktop\foodai\backend")
sys.path.insert(0, ".")

from app.db.session import SessionLocal
from app.services import stats as stats_service

db = SessionLocal()
try:
    # 模拟 stats/overview 查询用户 41
    result = stats_service.get_overview(db, user_id=41)
    print("OK:", result)
except Exception as e:
    print("ERROR:", e)
    traceback.print_exc()
finally:
    db.close()
