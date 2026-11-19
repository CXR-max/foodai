"""给演示账号补最近 N 天的完整数据（饮食 / 手环 / 打卡 / 体重）

用途：新环境或清库后，让首页的能量天平、健康评分、AI 教练、数据追踪、打卡日历
      立刻有内容，方便演示与验收。

用法：
    backend/.venv/Scripts/python.exe seed_week_demo.py
    backend/.venv/Scripts/python.exe seed_week_demo.py --user demo_xiaoming --days 7

特点：
- 幂等：先清掉该用户这几天里「本脚本口径」的数据再重写，重复执行不会翻倍
- 手环数据的 source 标成 demo，界面会显示「演示基线」，不冒充真实采集
- 只动指定的演示账号，不碰其他用户
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

from sqlalchemy import func

from app.db.session import SessionLocal
from app.models.check_in import CheckIn
from app.models.device import Device, DeviceMetric
from app.models.food_record import FoodRecord
from app.models.points_log import PointsLog
from app.models.user import User
from app.models.weight_log import WeightLog
from app.services import points

SEED_REF = "week_seed"          # 本脚本的积分流水标记，重跑时按它回收
HEIGHT_CM = 160

# 每天的步数与睡眠（从 7 天前到今天），刻意做出高低起伏，曲线才好看
DAILY = [
    # steps, sleep_min, deep_ratio, hr_avg, hr_rest
    (6820, 432, 0.22, 71, 61),
    (9350, 401, 0.19, 74, 62),
    (5120, 469, 0.24, 68, 59),
    (11040, 388, 0.18, 78, 63),
    (7640, 452, 0.21, 72, 60),
    (4380, 476, 0.25, 67, 58),
    (9210, 441, 0.20, 73, 61),
]

MEALS = {
    "breakfast": [
        ("燕麦牛奶碗", 480, 24, 14, 62),
        ("全麦三明治 + 无糖豆浆", 460, 22, 12, 60),
        ("小米粥 + 水煮蛋 + 凉拌黄瓜", 430, 20, 10, 62),
    ],
    "lunch": [
        ("杂粮饭 + 香煎鸡胸 + 西兰花", 780, 52, 16, 96),
        ("番茄牛腩盖饭", 820, 40, 26, 105),
        ("荞麦面 + 卤牛肉 + 青菜", 740, 44, 14, 100),
    ],
    "dinner": [
        ("清蒸鲈鱼 + 糙米饭 + 菠菜", 690, 46, 15, 84),
        ("虾仁豆腐 + 杂粮饭", 640, 42, 16, 78),
        ("香菇鸡腿 + 时蔬 + 半碗米饭", 700, 44, 22, 78),
    ],
    "snack": [
        ("无糖酸奶 + 蓝莓", 190, 10, 6, 22),
        ("苹果 + 一小把杏仁", 210, 5, 12, 26),
        ("蛋白棒", 180, 15, 6, 18),
    ],
}

EXERCISES = [
    ("快走", 40, 210.0),
    ("慢跑", 30, 300.0),
    ("瑜伽", 45, 150.0),
    ("骑行", 40, 280.0),
    ("HIIT", 20, 240.0),
    ("跳绳", 15, 200.0),
    ("羽毛球", 45, 260.0),
]


def _device(db, user: User) -> Device:
    dev = db.query(Device).filter(Device.user_id == user.id).first()
    if dev:
        return dev
    dev = Device(user_id=user.id, name="小米手环 10", model="Mi Band 10",
                 platform="xiaomi", status="bound", bind_source="manual")
    db.add(dev)
    db.flush()
    return dev


def _clear(db, user: User, start: date, end: date, device: Device) -> None:
    """清掉本脚本口径的旧数据（只删演示账号 + 这几天的）"""
    db.query(FoodRecord).filter(
        FoodRecord.user_id == user.id,
        FoodRecord.eaten_date >= start, FoodRecord.eaten_date <= end,
        FoodRecord.source == "manual",
    ).delete(synchronize_session=False)
    db.query(DeviceMetric).filter(
        DeviceMetric.user_id == user.id,
        DeviceMetric.metric_date >= start, DeviceMetric.metric_date <= end,
    ).delete(synchronize_session=False)
    db.query(CheckIn).filter(
        CheckIn.user_id == user.id,
        CheckIn.check_date >= start, CheckIn.check_date <= end,
    ).delete(synchronize_session=False)
    db.query(WeightLog).filter(
        WeightLog.user_id == user.id,
        WeightLog.recorded_date >= start, WeightLog.recorded_date <= end,
    ).delete(synchronize_session=False)
    # 回收上次脚本发的积分，避免重复执行把积分越刷越高
    old_logs = db.query(PointsLog).filter(
        PointsLog.user_id == user.id, PointsLog.ref_type == SEED_REF).all()
    if old_logs:
        delta = sum(p.points or 0 for p in old_logs)
        user.points_total = max(0, (user.points_total or 0) - delta)
        for p in old_logs:
            db.delete(p)
    db.flush()


def seed(username: str, days: int) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise SystemExit(f"找不到用户 {username}")

        today = date.today()
        start = today - timedelta(days=days - 1)
        device = _device(db, user)
        _clear(db, user, start, today, device)

        rows = DAILY[-days:] if days <= len(DAILY) else DAILY
        earned = 0
        for i in range(days):
            d = start + timedelta(days=i)
            steps, sleep_min, deep_ratio, hr_avg, hr_rest = rows[i]
            deep = round(sleep_min * deep_ratio)
            rem = round(sleep_min * 0.17)
            awake = 12 if i % 2 else 16
            light = max(0, sleep_min - deep - rem - awake)
            km = round(steps * (HEIGHT_CM * 0.415 / 100) / 1000, 2)
            kcal = round(km * 55 * 0.5, 1)

            db.add(DeviceMetric(
                user_id=user.id, device_id=device.id, metric_date=d,
                steps=steps, distance_km=km, calories=kcal,
                active_minutes=int(round(km / 4.5 * 60)),
                hr_avg=hr_avg, hr_min=hr_rest - 4, hr_max=118 + (i * 5) % 22,
                hr_resting=hr_rest,
                sleep_minutes=sleep_min, deep_sleep_minutes=deep,
                light_sleep_minutes=light, rem_sleep_minutes=rem,
                awake_minutes=awake,
                spo2=98, stress=32 + (i * 3) % 14,
                source="demo",
                raw={"seeded": True, "script": "seed_week_demo"},
            ))

            # 三餐 + 加餐：每天换一套，总量在 1900~2200 之间波动
            for idx, meal in enumerate(("breakfast", "lunch", "dinner", "snack")):
                name, cal, protein, fat, carb = MEALS[meal][(i + idx) % 3]
                eaten = datetime.combine(d, datetime.min.time()).replace(
                    hour={"breakfast": 8, "lunch": 12, "dinner": 19, "snack": 16}[meal])
                db.add(FoodRecord(
                    user_id=user.id, source="manual", dish_name=name,
                    ingredients=[], portion_g=350 if meal != "snack" else 150,
                    portion_desc="1 份", calories=cal, protein_g=protein,
                    fat_g=fat, carb_g=carb, meal_type=meal,
                    eaten_at=eaten, eaten_date=d, created_at=eaten,
                ))

            # 打卡：饮食 + 运动各一条，连续 7 天完成 → 首页连续打卡 7 天
            db.add(CheckIn(user_id=user.id, check_date=d, check_type="diet",
                           status="done", points_awarded=0, note="按方案吃了三餐"))
            ex, dur, burn = EXERCISES[i % len(EXERCISES)]
            ci = CheckIn(user_id=user.id, check_date=d, check_type="exercise",
                         status="done", exercise_type=ex, duration_min=dur,
                         calories_burned=burn, intensity="moderate", met_used=6.0,
                         points_awarded=0, note=f"{ex} {dur} 分钟")
            db.add(ci)
            db.flush()
            pts = points.award(db, user, "manual_checkin", description=f"{d} 完成打卡",
                               points=5, ref_type=SEED_REF, ref_id=ci.id)
            earned += pts or 0

            # 体重：轻微下降趋势，让体重曲线有得看
            db.add(WeightLog(user_id=user.id, recorded_date=d,
                             weight_kg=round(55.6 - i * 0.09, 1), note="晨起空腹"))

        device.last_sync_at = datetime.now()
        device.last_sync_source = "demo"
        device.auto_sync = True
        db.commit()

        total_food = db.query(func.count(FoodRecord.id)).filter(
            FoodRecord.user_id == user.id,
            FoodRecord.eaten_date >= start, FoodRecord.eaten_date <= today).scalar()
        print(f"[完成] {username}：{days} 天数据")
        print(f"  手环指标 {days} 天 / 饮食记录 {total_food} 条 / 打卡 {days * 2} 条 / 体重 {days} 条")
        print(f"  本次发放积分 {earned}，当前总积分 {user.points_total}")
        print(f"  日期范围 {start} ~ {today}")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="demo_xiaoming")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    seed(args.user, args.days)
