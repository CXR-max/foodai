import sys, os, traceback
os.chdir(r"C:\Users\43120\Desktop\foodai\backend")
sys.path.insert(0, ".")

from app.db.session import SessionLocal
from app.models.check_in import CheckIn
from app.models.food_record import FoodRecord
from app.models.health_profile import HealthProfile
from app.models.user import User
from sqlalchemy import func

db = SessionLocal()
try:
    print("=== 测试查询 today_cal ===")
    today_cal = db.query(func.coalesce(func.sum(FoodRecord.calories), 0.0)).filter(
        FoodRecord.user_id == 41).scalar()
    print(f"OK: {today_cal}")

    print("=== 测试查询 today_burn ===")
    today_burn = db.query(func.sum(CheckIn.calories_burned)).filter(
        CheckIn.user_id == 41).scalar()
    print(f"OK: {today_burn}")
except Exception as e:
    print(f"ERROR: {e}")
    traceback.print_exc()
finally:
    db.close()
