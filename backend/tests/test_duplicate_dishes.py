"""同一道菜不许一天吃两遍

背景：老方案（plan_version 1、没有落 targets/nutrition）是走大模型出的菜单，
模型会把「水煮鱼、馒头」原样塞进午餐和晚餐。用户从历史方案里把它导入日程时，
旧代码只把日期整体顺延，菜单一个字不动，于是 11 月吃到的是 9 月的重复单子。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  确保所有表都注册到 Base.metadata
from app.db.session import Base
from app.models.diet_plan import DietPlan, PlanDay
from app.routers.plans import (
    _day_needs_refresh, _refresh_stale_days, repeated_dishes,
)

SNAPSHOT = {
    "height_cm": 165.0, "weight_kg": 55.0, "age": 19, "gender": "female",
    "activity_level": "light", "goal": "maintain", "daily_target_calories": 1821,
    "taste_preferences": ["蒜香"], "dietary_restrictions": ["花生过敏"],
}
TARGETS = {
    "calorie_target": 1821, "calorie_min": 1640, "calorie_max": 1910,
    "protein_min_g": 66, "protein_max_g": 120, "fat_min_g": 40, "fat_max_g": 61,
    "carb_min_g": 220, "carb_max_g": 280, "fiber_min_g": 25,
    "sodium_max_mg": 2000, "sugar_max_mg": 50,
}


def _meal(meal_type: str, names: list[str]) -> dict:
    return {
        "meal_type": meal_type, "name": meal_type, "calories": 400,
        "items": [{"name": name, "portion_g": 150} for name in names],
    }


@pytest.fixture(scope="module")
def session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory


# ---------- 判定 ----------

def test_same_dish_in_lunch_and_dinner_is_repeated():
    meals = [
        _meal("breakfast", ["小米粥"]),
        _meal("lunch", ["水煮鱼", "馒头"]),
        _meal("dinner", ["水煮鱼", "馒头"]),
        _meal("snack", ["关东煮"]),
    ]
    assert sorted(repeated_dishes(meals)) == ["水煮鱼", "馒头"]


def test_two_servings_in_one_meal_is_not_repeated():
    """一餐里点两份同款（200g 拆成两份）不算跨餐重复"""
    meals = [
        _meal("lunch", ["米饭", "米饭", "青菜"]),
        _meal("dinner", ["鸡胸肉"]),
    ]
    assert repeated_dishes(meals) == []


def test_clean_day_has_no_duplicate():
    meals = [
        _meal("lunch", ["米饭", "鸡胸肉"]),
        _meal("dinner", ["糙米饭", "西兰花"]),
    ]
    assert repeated_dishes(meals) == []


# ---------- 旧菜单体检 ----------

def _stale_plan(db, days: int = 3) -> DietPlan:
    """造一份和线上 plan 12 同款的旧方案：没有 targets / nutrition，午晚餐一模一样"""
    plan = DietPlan(
        user_id=1, title="旧方案", status="active",
        basis_snapshot=dict(SNAPSHOT), targets=None, plan_version=1,
    )
    db.add(plan)
    db.flush()
    for index in range(1, days + 1):
        db.add(PlanDay(
            plan_id=plan.id, day_index=index,
            meals=[
                _meal("breakfast", ["小米粥"]),
                _meal("lunch", ["水煮鱼", "馒头"]),
                _meal("dinner", ["水煮鱼", "馒头"]),
                _meal("snack", ["关东煮"]),
            ],
            targets=None, nutrition=None,
        ))
    db.commit()
    return plan


def test_stale_day_is_detected():
    plan = type("P", (), {"meals": [_meal("lunch", ["米饭"]), _meal("dinner", ["米饭"])],
                          "targets": TARGETS, "nutrition": {"calories": 1800}})()
    assert _day_needs_refresh(plan) is True


def test_import_style_refresh_rewrites_duplicated_menu(session_factory):
    """顺延过来的旧菜单：必须按当前营养库重排，且重排后午餐晚餐不再一样"""
    db = session_factory()
    try:
        plan = _stale_plan(db)
        days = (db.query(PlanDay).filter(PlanDay.plan_id == plan.id)
                .order_by(PlanDay.day_index.asc()).all())

        refreshed = _refresh_stale_days(db, plan, days)

        assert refreshed == [1, 2, 3], "旧菜单每一天都该被重排"
        assert plan.plan_version == 2
        assert "重排" in (plan.summary or "")
        assert plan.targets and plan.targets.get("calorie_min")

        for day in days:
            assert day.targets, "重排后要补上当日目标，否则日程页算不出指标"
            assert day.nutrition, "重排后要落七项，否则详情页显示空"
            assert repeated_dishes(day.meals or []) == [], f"第 {day.day_index} 天仍有跨餐重复"
    finally:
        db.close()


def test_healthy_day_is_left_alone(session_factory):
    """没过期、没重复、七项齐全的天不许动——顺延两天就把菜全换掉是另一种伤害"""
    db = session_factory()
    try:
        plan = DietPlan(
            user_id=1, title="正常方案", status="active",
            basis_snapshot=dict(SNAPSHOT), targets=dict(TARGETS), plan_version=1,
        )
        db.add(plan)
        db.flush()
        db.add(PlanDay(
            plan_id=plan.id, day_index=1,
            meals=[_meal("lunch", ["米饭", "鸡胸肉"]), _meal("dinner", ["糙米饭", "西兰花"])],
            targets=dict(TARGETS), nutrition={"calories": 1800},
        ))
        db.commit()
        days = db.query(PlanDay).filter(PlanDay.plan_id == plan.id).all()

        assert _refresh_stale_days(db, plan, days) == []
        assert plan.plan_version == 1
    finally:
        db.close()
