"""动态调整：计划跟着实际执行走，而不是排完就不管

覆盖四条线（依据见 services/adapt.py 模块注释）：
- 睡眠不足 → 次日不排中等以上强度，入睡建议提前 30 分钟
- 饮水不足 → 次日饮水目标上调 10%
- 蛋白长期不够 → 蛋白下限上提（封顶 2.0 g/kg）
- 运动超额 → 恢复日
每条调整都要在 day_flags.adapted 留痕（为什么改），且当天幂等（不重复应用）。
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  确保所有表都注册到 Base.metadata
from app.db.session import Base
from app.models.diet_plan import DietPlan, PlanDay
from app.models.food_record import FoodRecord
from app.models.sleep_log import SleepLog
from app.models.water_log import WaterLog
from app.services import adapt

TODAY = date.today()

SNAPSHOT = {
    "height_cm": 165.0, "weight_kg": 55.0, "age": 19, "gender": "female",
    "activity_level": "light", "goal": "maintain",
    "sleep_time": "23:30", "wake_time": "07:00",
}


def _exercise(intensity: str = "moderate", minutes: int = 40, burn: int = 220) -> dict:
    return {"type": "慢跑", "duration_min": minutes, "intensity": intensity,
            "calories_burned": burn}


def _plan_with_days(db, n: int = 3, intensity: str = "moderate") -> DietPlan:
    plan = DietPlan(user_id=1, title="测试方案", status="active",
                    basis_snapshot=dict(SNAPSHOT), plan_version=2)
    db.add(plan)
    db.flush()
    for index in range(1, n + 1):
        db.add(PlanDay(
            plan_id=plan.id, day_index=index, date=TODAY + timedelta(days=index - 1),
            meals=[], exercise=None, exercises=[_exercise(intensity)],
            sleep={"bedtime": "23:30", "planned_h": 7.5},
            water_ml=2000,
            targets={"sleep_min_h": 7.0, "water_min_ml": 2000,
                     "protein_min_g": 66, "fiber_min_g": 25,
                     "exercise_burn_min": 180, "exercise_burn_max": 320,
                     "calorie_min": 1600, "calorie_max": 1900,
                     "calorie_target": 1750, "bmr": 1200, "tdee": 1600},
        ))
    db.commit()
    return plan


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def _short_sleep(db):
    """前两晚都只睡 5 小时，且入睡过了 23 点（bedtime 是 DateTime 列）"""
    from datetime import datetime
    for offset in (2, 1):
        base = datetime.combine(TODAY - timedelta(days=offset), datetime.min.time())
        db.add(SleepLog(user_id=1, sleep_date=TODAY - timedelta(days=offset),
                        bedtime=base.replace(hour=23, minute=50),
                        wake_at=base.replace(hour=4, minute=50),
                        duration_min=300))
    db.commit()


def _low_water(db):
    for offset in (2, 1):
        db.add(WaterLog(user_id=1, log_date=TODAY - timedelta(days=offset), ml=900))
    db.commit()


def _low_protein(db):
    for offset in (2, 1):
        db.add(FoodRecord(user_id=1, dish_name="随便吃的", calories=900,
                          protein_g=40, fiber_g=10, sodium_mg=1500,
                          eaten_date=TODAY - timedelta(days=offset),
                          voided_at=None))
    db.commit()


def _profile(db):
    """处置重排走 HealthProfile 读档案，必须有这一行"""
    from app.models.health_profile import HealthProfile
    db.add(HealthProfile(user_id=1, height_cm=165.0, weight_kg=55.0, age=19,
                         gender="female", activity_level="light", goal="maintain",
                         sleep_time="23:30", wake_time="07:00"))
    db.commit()


def _over_eat(db):
    """近两天日均 2600 kcal，超上限 1900 达 700"""
    for offset in (2, 1):
        db.add(FoodRecord(user_id=1, dish_name="大餐", calories=2600,
                          protein_g=90, fiber_g=30, sodium_mg=2500,
                          eaten_date=TODAY - timedelta(days=offset), voided_at=None))
    db.commit()


def _under_eat(db):
    """近两天日均 1300 kcal：低于下限 85%（1360）但没掉到代谢地板（1280）以下"""
    for offset in (2, 1):
        db.add(FoodRecord(user_id=1, dish_name="吃不下", calories=1300,
                          protein_g=55, fiber_g=18, sodium_mg=1600,
                          eaten_date=TODAY - timedelta(days=offset), voided_at=None))
    db.commit()


def test_short_sleep_downgrades_exercise_and_shifts_bedtime(db):
    """熬夜两晚 → 今天的跑步改恢复强度、入睡建议提前"""
    _plan_with_days(db, 2)
    _short_sleep(db)

    result = adapt.apply(db, 1, TODAY)
    day = (db.query(PlanDay).filter(PlanDay.day_index == 1).one())

    assert day.exercises, "恢复日也要有运动项"
    assert day.exercises[0]["intensity"] == "light", "睡眠不足不允许中等以上强度"
    assert day.exercises[0]["type"] in ("散步", "快走", "拉伸", "瑜伽")
    assert day.sleep["bedtime"] == "23:00", "入睡提前 30 分钟"
    # 恢复日消耗低于原下限，判定区间跟着降，不能一边改散步一边按原消耗判不足
    assert day.targets["exercise_burn_min"] <= day.exercises[0]["calories_burned"]
    reasons = day.day_flags["adapted"]["reasons"]
    assert any(r["field"] == "exercise" for r in reasons)
    assert any(r["field"] == "sleep" for r in reasons)
    assert result["applied"], "要留痕应用了哪几天"


def test_low_water_raises_target(db):
    _plan_with_days(db, 2)
    _low_water(db)

    adapt.apply(db, 1, TODAY)
    day = (db.query(PlanDay).filter(PlanDay.day_index == 1).one())

    assert day.water_ml == 2200, "饮水目标上调 10%"
    assert day.targets["water_min_ml"] == 2200, "判定区间同步，别一边加目标一边按旧值判不足"
    assert any(r["field"] == "water" for r in day.day_flags["adapted"]["reasons"])


def test_low_protein_raises_floor_with_cap(db):
    _plan_with_days(db, 2)
    _low_protein(db)

    adapt.apply(db, 1, TODAY)
    day = (db.query(PlanDay).filter(PlanDay.day_index == 1).one())

    assert day.targets["protein_min_g"] == 73, "蛋白下限上提 10%（66 → 73）"
    reason = next(r for r in day.day_flags["adapted"]["reasons"] if r["field"] == "diet")
    assert "蛋白" in reason["action"]


def test_apply_is_idempotent_within_a_day(db):
    """同一天重复触发（用户反复刷新日程页）不叠加、不覆盖"""
    _plan_with_days(db, 2)
    _short_sleep(db)

    adapt.apply(db, 1, TODAY)
    first = (db.query(PlanDay).filter(PlanDay.day_index == 1).one())
    bedtime_after_first = first.sleep["bedtime"]
    water_after_first = first.water_ml

    adapt.apply(db, 1, TODAY)
    db.refresh(first)
    assert first.sleep["bedtime"] == bedtime_after_first
    assert first.water_ml == water_after_first


def test_no_change_when_execution_is_fine(db):
    """执行都达标 → 不动方案，也没有 adapted 留痕"""
    _plan_with_days(db, 2)
    # 睡够、水够、蛋白够
    from datetime import datetime
    for offset in (2, 1):
        base = datetime.combine(TODAY - timedelta(days=offset), datetime.min.time())
        db.add(SleepLog(user_id=1, sleep_date=TODAY - timedelta(days=offset),
                        bedtime=base.replace(hour=23, minute=0),
                        wake_at=base.replace(hour=6, minute=30),
                        duration_min=450))
        db.add(WaterLog(user_id=1, log_date=TODAY - timedelta(days=offset), ml=2200))
        db.add(FoodRecord(user_id=1, dish_name="按方案吃的", calories=1800,
                          protein_g=75, fiber_g=28, sodium_mg=1800,
                          eaten_date=TODAY - timedelta(days=offset), voided_at=None))
    db.commit()

    result = adapt.apply(db, 1, TODAY)
    assert not result["applied"], "执行正常就不该有调整"
    day = (db.query(PlanDay).filter(PlanDay.day_index == 1).one())
    assert day.exercises[0]["intensity"] == "moderate", "运动强度不被乱改"
    assert "adapted" not in (day.day_flags or {})


def test_overreached_exercise_gets_recovery_day(db):
    """连续超额运动 → 今天恢复，不做惩罚性加练"""
    _plan_with_days(db, 2, intensity="moderate")
    # 之前两天的运动消耗都远超上限 320
    from app.models.check_in import CheckIn
    for offset in (2, 1):
        db.add(CheckIn(user_id=1, check_date=TODAY - timedelta(days=offset),
                       check_type="exercise", task_key="exercise",
                       exercise_type="慢跑", status="done",
                       calories_burned=500, duration_min=60))
    db.commit()

    adapt.apply(db, 1, TODAY)
    day = (db.query(PlanDay).filter(PlanDay.day_index == 1).one())
    assert day.exercises[0]["intensity"] == "light"
    assert any("恢复" in r["reason"] for r in day.day_flags["adapted"]["reasons"])


# ==================== 摄入热量线：吃多了 / 吃少了 ====================

def test_over_eating_trims_band(db):
    """连续两天日均 2600 → 后续几天热量区间下移（地板守卫），留 needs_replan 与 before 快照"""
    _plan_with_days(db, 3)
    _over_eat(db)

    adapt.apply(db, 1, TODAY, days=2, kick=False)
    day = (db.query(PlanDay).filter(PlanDay.day_index == 1).one())

    # 区间下移 175/天，地板 max(1200, 1600×0.8)=1280 不触底
    assert day.targets["calorie_min"] == 1425
    assert day.targets["calorie_max"] == 1725
    flags = day.day_flags["adapted"]
    assert flags["needs_replan"] is True, "只改数字不重排就是空壳，必须标记重排"
    assert flags["before"]["targets"]["calorie_min"] == 1600
    reason = next(r for r in flags["reasons"] if r["field"] == "diet")
    assert "热量区间" in reason["action"]


def test_over_eating_replans_meals(db):
    """标记 needs_replan → replan_pending 真正按新区间换菜重排，热量落回新区间"""
    _plan_with_days(db, 3)
    _profile(db)
    _over_eat(db)

    adapt.apply(db, 1, TODAY, days=2, kick=False)
    replanned = adapt.replan_pending(db, 1, TODAY, days=2)

    assert replanned and all(item["ok"] for item in replanned), "求解器要能按新区间出方案"
    db.expire_all()
    day = (db.query(PlanDay).filter(PlanDay.day_index == 1).one())
    assert day.meals, "重排后要有菜"
    assert day.day_flags["adapted"]["replanned"] is True
    assert day.day_flags["replanned"]["reason"].startswith("动态调整")
    calories = (day.nutrition or {}).get("calories") or 0
    assert 1425 <= calories <= 1725, f"重排后热量 {calories} 应落回新区间"


def test_under_eating_calibrates_band(db):
    """连续吃不完 → 方案下沿校准到可执行水平（不下探代谢地板、不逼补吃）"""
    _plan_with_days(db, 3)
    _under_eat(db)

    adapt.apply(db, 1, TODAY, days=2, kick=False)
    day = (db.query(PlanDay).filter(PlanDay.day_index == 1).one())

    # 日均 1300，缺口 300 → 每天 150，new_min 1450 > 地板 1280
    assert day.targets["calorie_min"] == 1450
    assert day.targets["calorie_max"] == 1750
    reason = next(r for r in day.day_flags["adapted"]["reasons"] if "校准" in r["action"])
    assert "补吃" in reason["reason"], "理由要说清不逼用户补吃"


def test_under_eating_below_floor_keeps_band(db):
    """摄入已经低于代谢地板 → 方案不再往下压热量（安全底线）"""
    _plan_with_days(db, 2)
    for offset in (2, 1):
        db.add(FoodRecord(user_id=1, dish_name="几乎没吃", calories=900,
                          protein_g=60, fiber_g=20, sodium_mg=1200,
                          eaten_date=TODAY - timedelta(days=offset), voided_at=None))
    db.commit()

    adapt.apply(db, 1, TODAY, days=2, kick=False)
    day = (db.query(PlanDay).filter(PlanDay.day_index == 1).one())
    assert day.targets["calorie_min"] == 1600, "吃得已低于代谢地板，下限不能跟着往下压"


def test_revert_restores_snapshot(db):
    """撤销动态调整：目标/区间原样恢复，二次撤销报错"""
    _plan_with_days(db, 2)
    _over_eat(db)

    adapt.apply(db, 1, TODAY, days=2, kick=False)
    day2_before = (db.query(PlanDay).filter(PlanDay.day_index == 2).one())

    result = adapt.revert_day(db, 1, TODAY + timedelta(days=1))
    assert result["reverted"]
    db.expire_all()
    day2 = (db.query(PlanDay).filter(PlanDay.day_index == 2).one())
    assert day2.targets["calorie_min"] == day2_before.day_flags["adapted"]["before"]["targets"]["calorie_min"] == 1600
    assert day2.day_flags["adapted"]["reverted"] is True

    with pytest.raises(ValueError):
        adapt.revert_day(db, 1, TODAY + timedelta(days=1))


def test_disposition_loosen_next_days(db):
    """手动处置「吃少了」→ 后面几天按实际食量校准并重排（补齐旧窗口缺的方向）"""
    from app.models.diet_deviation import DietDeviation
    from app.services import disposition

    _plan_with_days(db, 3)
    _profile(db)
    dev = DietDeviation(user_id=1, dev_date=TODAY, nutrient="calorie", kind="under",
                        actual=1200, limit_value=1600, delta=400, attribution="intake",
                        severity="hard", status="open", settlement="final")
    db.add(dev)
    db.commit()
    targets = {"calorie_min": 1600, "calorie_max": 1900, "calorie_target": 1750,
               "bmr": 1200, "tdee": 1600, "weight_kg": 55, "exercise_burn_max": 320}

    result = disposition.apply(db, 1, dev, "next_days_loosen",
                               {"days": 2, "per_day": 200}, targets=targets)
    assert result["touched"], "后面几天要有可调空间"
    assert result["touched"][0]["replanned"], "只改数字不重排等于没改"
    db.expire_all()
    day2 = (db.query(PlanDay).filter(PlanDay.day_index == 2).one())
    assert day2.targets["calorie_min"] == 1400
    assert day2.targets["calorie_max"] == 1700
    assert dev.status == "handled"
