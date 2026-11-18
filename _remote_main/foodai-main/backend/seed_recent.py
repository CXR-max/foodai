"""给演示账号补最近 7 天数据：饮食记录（含营养素）、运动打卡（MET 算消耗）、体重曲线
只写 demo_xiaoming，可重复执行（先清旧的 7 天内记录再写入）。
"""
import random
from datetime import date, datetime, timedelta

from app.db.session import SessionLocal
from app.models.check_in import CheckIn
from app.models.food_record import FoodRecord
from app.models.user import User
from app.models.weight_log import WeightLog
from app.services.met import calc_burn

random.seed(42)

MEALS = [
    ("breakfast", "燕麦粥+水煮蛋", 350, 320, 18, 8, 52),
    ("lunch", "清蒸鲈鱼+糙米饭", 450, 560, 38, 12, 68),
    ("dinner", "鸡胸肉沙拉", 380, 410, 35, 14, 32),
    ("snack", "苹果+酸奶", 200, 180, 6, 4, 30),
]
EXERCISES = [("跑步", 30), ("快走", 40), ("骑行", 45), ("瑜伽", 30), ("跳绳", 20), ("力量训练", 40)]

db = SessionLocal()
user = db.query(User).filter(User.username == "demo_xiaoming").first()
weight_kg = 62.0

today = date.today()
start = today - timedelta(days=6)

# 清掉该用户 7 天内的旧记录（避免重复叠加）
for m in (FoodRecord, CheckIn, WeightLog):
    q = db.query(m).filter(m.user_id == user.id)
    date_col = getattr(m, "eaten_date", None) or getattr(m, "check_date", None) or getattr(m, "recorded_date")
    if date_col is not None:
        db.query(m).filter(m.user_id == user.id, date_col >= start).delete(synchronize_session=False)
db.commit()

for i in range(7):
    d = start + timedelta(days=i)
    # 饮食：每天 3~4 餐
    for meal in MEALS[: random.choice([3, 4, 4])]:
        _, name, portion, kcal, p, f, c = meal
        jitter = random.uniform(0.85, 1.15)
        db.add(FoodRecord(
            user_id=user.id, source="manual", dish_name=name, portion_g=portion,
            calories=round(kcal * jitter, 1), protein_g=round(p * jitter, 1),
            fat_g=round(f * jitter, 1), carb_g=round(c * jitter, 1),
            meal_type=meal[0], eaten_at=datetime.combine(d, datetime.min.time()).replace(hour=12),
            eaten_date=d,
        ))
    # 运动：一周练 5 天
    if i not in (2, 5):
        etype, dur = EXERCISES[i % len(EXERCISES)]
        burn = calc_burn(etype, dur, weight_kg)
        db.add(CheckIn(
            user_id=user.id, check_date=d, check_type="exercise", task_key="manual_exercise",
            status="done", exercise_type=etype, duration_min=dur, calories_burned=burn,
            created_at=datetime.combine(d, datetime.min.time()).replace(hour=18),
        ))
        db.add(CheckIn(
            user_id=user.id, check_date=d, check_type="diet", task_key="manual_food",
            status="done", created_at=datetime.combine(d, datetime.min.time()).replace(hour=9),
        ))
    # 体重：缓慢下降
    w = round(weight_kg - i * 0.25 + random.uniform(-0.2, 0.2), 1)
    db.add(WeightLog(user_id=user.id, weight_kg=w, recorded_date=d))

db.commit()
print("已为", user.username, "写入最近 7 天演示数据")
print("验证：饮食记录", db.query(FoodRecord).filter(FoodRecord.user_id == user.id,
      FoodRecord.eaten_date >= start).count(), "条；运动打卡",
      db.query(CheckIn).filter(CheckIn.user_id == user.id, CheckIn.check_date >= start,
                               CheckIn.check_type == "exercise").count(), "次")
db.close()
