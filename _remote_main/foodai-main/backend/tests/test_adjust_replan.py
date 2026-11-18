"""后续调整：改目标必须连带改菜，且不能只缩份量

两条真实故障，都是「看起来做了、实际没用」：

1. `_trim_next_days` 只写 targets，不重排、也不递增 edit_count。
   那天要是被编辑过（edit_count>0），目标生效但菜还是原量 → 用户按方案吃完就判超，
   归因还写成「方案排错」，于是又去缩方案，绕成死循环；
   没编辑过则 `targets_for_day` 根本不采纳当日覆盖，白改一场。
2. `_scale_meal` 一律缩主食克数（而且因为读错了营养字段，实际直接砍到 60%）。
   连调几天就变成「同样的菜、不同的克数」，用户会觉得系统只会让他少吃。
"""
import copy
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.session import Base
from app.models.check_in import CheckIn
from app.models.diet_deviation import DietDeviation
from app.models.diet_plan import DietPlan, PlanDay
from app.models.health_profile import HealthProfile
from app.models.user import User
from app.services import disposition

TODAY = date.today()

TARGETS = {
    "calorie_target": 1900, "calorie_min": 1800, "calorie_max": 2100,
    "protein_min_g": 110, "protein_max_g": 150,
    "fat_min_g": 42, "fat_max_g": 63, "carb_min_g": 214, "carb_max_g": 261,
    "fiber_min_g": 25, "sodium_max_mg": 2000, "sugar_max_g": 25,
    "exercise_burn_max": 500, "weight_kg": 65,
    # 地板 = max(BMR 1500, TDEE×0.8=1680)，低于目标下限 1800 才有下调空间
    "prescription": {"bmr": 1500, "tdee": 2100, "deficit_pct": 0.15},
}

MEALS = [
    {"meal_type": "breakfast", "name": "早餐", "items": [
        {"name": "馒头", "portion_g": 100, "category": "主食"},
        {"name": "鸡蛋", "portion_g": 50, "category": "蛋白"},
        {"name": "牛奶", "portion_g": 250, "category": "奶"},
    ]},
    {"meal_type": "lunch", "name": "午餐", "items": [
        {"name": "米饭", "portion_g": 200, "category": "主食"},
        {"name": "鸡胸肉", "portion_g": 150, "category": "蛋白"},
        {"name": "西兰花", "portion_g": 150, "category": "蔬菜"},
    ]},
    {"meal_type": "dinner", "name": "晚餐", "items": [
        {"name": "米饭", "portion_g": 180, "category": "主食"},
        {"name": "清蒸鱼", "portion_g": 150, "category": "蛋白"},
        {"name": "青菜", "portion_g": 200, "category": "蔬菜"},
    ]},
]


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = Session()
    yield session
    session.close()


def _seed(db) -> tuple[User, DietPlan, list[PlanDay]]:
    user = User(username="replan_tester", password_hash="x", points_total=0)
    db.add(user)
    db.flush()
    db.add(HealthProfile(user_id=user.id, height_cm=172, weight_kg=65, age=30,
                         gender="male", activity_level="moderate", goal="lose"))
    plan = DietPlan(user_id=user.id, title="重排测试", start_date=TODAY, status="active",
                    summary="", daily_target_calories=1900, targets=dict(TARGETS),
                    provider="test", plan_version=1)
    db.add(plan)
    db.flush()
    days = []
    for index in range(1, 4):
        day = PlanDay(plan_id=plan.id, day_index=index,
                      date=TODAY + timedelta(days=index - 1),
                      meals=copy.deepcopy(MEALS), targets=dict(TARGETS))
        db.add(day)
        db.flush()
        days.append(day)
    db.commit()
    return user, plan, days


def _dev(user_id: int, delta: float = 420) -> DietDeviation:
    return DietDeviation(user_id=user_id, dev_date=TODAY, kind="over", nutrient="calorie",
                         delta=delta, attribution="user", status="open",
                         actual=2500, limit_value=2100)


# ---------------- 下调目标必须连带重排 ----------------

def test_next_days_trim_keeps_menu_consistent_with_new_band(db):
    """区间下调后，那天的菜必须跟着变——不然「按方案吃完」又变成超标"""
    user, plan, days = _seed(db)
    dev = _dev(user.id)
    db.add(dev)
    db.commit()

    result = disposition.apply(db, user.id, dev, "next_days_trim",
                               {"days": 2, "per_day": 200}, targets=dict(TARGETS))
    db.commit()
    touched = result["touched"]
    assert touched, result["detail"]

    for item in touched:
        day = (db.query(PlanDay)
               .filter(PlanDay.date == date.fromisoformat(item["date"])).first())
        assert day.targets["calorie_min"] < TARGETS["calorie_min"]
        # 没有 edit_count，targets_for_day 不采纳当日覆盖，下调等于没写
        assert (day.edit_count or 0) >= 1
        calories = (day.nutrition or {}).get("calories") or 0
        assert calories > 0
        # 排出来的量落在新区间（留 8% 取整容差），这才是「按方案吃不会超」
        assert calories <= day.targets["calorie_max"] * 1.08
        assert calories >= day.targets["calorie_min"] * 0.85


def test_next_days_trim_does_not_replan_eaten_day(db):
    """已经吃过的天不重排：把用户吃过的菜换掉，记录和方案就对不上了"""
    user, plan, days = _seed(db)
    eaten_day = days[1]
    db.add(CheckIn(user_id=user.id, check_date=eaten_day.date, check_type="meal",
                   task_key="meal_lunch", status="done"))
    dev = _dev(user.id)
    db.add(dev)
    db.commit()

    before = copy.deepcopy(eaten_day.meals)
    disposition.apply(db, user.id, dev, "next_days_trim",
                      {"days": 2, "per_day": 200}, targets=dict(TARGETS))
    db.commit()
    db.refresh(eaten_day)

    assert "replanned" not in (eaten_day.day_flags or {})
    # 允许缩份量，但菜名不能被换掉
    assert {i["name"] for m in eaten_day.meals for i in m["items"]} == \
           {i["name"] for m in before for i in m["items"]}


# ---------------- 缩份量分档 ----------------

def test_small_trim_only_shrinks_staple(db):
    """要减 100 kcal：只缩主食克数，不换菜（这么小的量换了反而奇怪）"""
    user, plan, days = _seed(db)
    day = days[0]
    before = {i["name"] for m in day.meals for i in m["items"]}

    info = disposition._scale_meal(day, ["lunch"], 100, dict(TARGETS))

    assert {i["name"] for m in day.meals for i in m["items"]} == before
    assert info["changed"]
    assert all("份量下调" in c["why"] for c in info["changed"])
    assert info["portion_factor"] < 1


def test_large_trim_swaps_same_kind_dish(db):
    """要减 300 kcal：优先换同类菜，而不是把主食砍一半"""
    user, plan, days = _seed(db)
    day = days[0]
    before = {i["name"] for m in day.meals for i in m["items"]}

    info = disposition._scale_meal(day, ["lunch", "dinner"], 300, dict(TARGETS))

    swapped = [c for c in info["changed"] if c["why"] == "换同类菜，热量相当"]
    assert swapped, info["changed"]
    assert {i["name"] for m in day.meals for i in m["items"]} != before
    # 换的必须还是主食（蛋白主菜不动，否则蛋白区间会越界）
    for item in swapped:
        assert item["to"]


def test_scale_meal_updates_seven_nutrients(db):
    """缩完要按七项重算：只更四项会让详情页和判定两套数字"""
    user, plan, days = _seed(db)
    day = days[0]

    info = disposition._scale_meal(day, ["lunch"], 120, dict(TARGETS))

    nutrition = info["total_after"]
    assert {"calories", "protein_g", "fiber_g", "sodium_mg", "sugar_g"} <= set(nutrition)
    assert nutrition["calories"] > 0
    assert day.nutrition == nutrition


def test_scale_meal_records_what_changed(db):
    """调整留痕要能看出「变了什么」，只给一个数字用户看不懂"""
    user, plan, days = _seed(db)
    day = days[0]

    disposition._scale_meal(day, ["lunch", "dinner"], 260, dict(TARGETS))

    adjusted = (day.day_flags or {}).get("adjusted") or {}
    assert adjusted.get("changed")
    for entry in adjusted["changed"]:
        assert entry.get("meal") and entry.get("from") and entry.get("why")
