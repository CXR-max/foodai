"""今日体检清单：九项逐条判 + 「现在能做什么」「后续会怎么调」

覆盖点：
- 达标日不误报（九项全在区间内 → failed 为空）
- 蛋白不足 / 钠超限 → 进 failed，并带 gap_text 与 now
- 还有餐没打卡 → 热量蛋白判「进行中」，不算没达标
- 饮水/睡眠不足 → 进 failed
- 食物种类不足 → 按 canonical 名去重计数
- next_plan 里的动作要贴到对应项上（later 不是空话）

维生素不做假指标：这里没有 vitamin 项，是有意的（食物库 extra_nutrients 全空）。
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  确保所有表都注册到 Base.metadata
from app.db.session import Base
from app.models.check_in import CheckIn
from app.models.diet_plan import DietPlan, PlanDay
from app.models.food_record import FoodRecord
from app.services import daily_audit

TODAY = date.today()

TARGETS = {
    "calorie_min": 1600, "calorie_max": 1900, "calorie_target": 1750,
    "protein_min_g": 62, "protein_max_g": 105,
    "fiber_min_g": 25, "sodium_max_mg": 2000, "sugar_max_g": 25,
    "water_min_ml": 2000, "water_max_ml": 3000,
    "sleep_min_h": 7.0, "sleep_max_h": 9.0,
    "exercise_burn_min": 180, "exercise_burn_max": 320,
}


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


def _tasks(db, done=("breakfast", "lunch", "dinner"), pending=()):
    for meal in done:
        db.add(CheckIn(user_id=1, check_date=TODAY, check_type="meal",
                       task_key=f"meal_{meal}", status="done"))
    for meal in pending:
        db.add(CheckIn(user_id=1, check_date=TODAY, check_type="meal",
                       task_key=f"meal_{meal}", status="pending"))
    db.add(CheckIn(user_id=1, check_date=TODAY, check_type="exercise",
                   task_key="exercise", status="done", calories_burned=240,
                   duration_min=40, exercise_type="慢跑"))
    db.commit()
    return db.query(CheckIn).filter(CheckIn.user_id == 1).all()


def _intake(**over):
    base = {"calories": 1800, "protein_g": 80, "fiber_g": 28,
            "sodium_mg": 1800, "sugar_g": 15, "records": 3}
    base.update(over)
    return base


def _ctx(db, tasks, **over):
    ctx = {
        "targets": dict(TARGETS),
        "band_targets": dict(TARGETS),
        "intake": _intake(),
        "burn": {"exercise_kcal": 240, "exercise_min": 40, "times": 1},
        "water_ml": 2200,
        "sleep_h": 7.5,
        "tasks": tasks,
        "plan_day": None,
        "settled": True,
    }
    ctx.update(over)
    return ctx


def _keys(result):
    return {item["key"] for item in result["failed"]}


def test_all_in_range_reports_nothing_failed(db):
    tasks = _tasks(db)
    result = daily_audit.audit(db, 1, TODAY, _ctx(db, tasks))

    assert result["failed"] == []
    assert result["ok"] is True
    assert result["checked"] >= 7
    assert "全部达标" in result["headline"]


def test_protein_under_and_sodium_over_are_flagged(db):
    tasks = _tasks(db)
    ctx = _ctx(db, tasks, intake=_intake(protein_g=45, sodium_mg=3200))
    result = daily_audit.audit(db, 1, TODAY, ctx)

    assert {"protein", "sodium"} <= _keys(result)
    protein = next(i for i in result["failed"] if i["key"] == "protein")
    # 差多少要说出来，不能只给个红字
    assert protein["gap_text"] and "17" in protein["gap_text"]
    assert protein["now"], "每项都要给「现在能做什么」"
    assert protein["severity"] == "high"
    sodium = next(i for i in result["failed"] if i["key"] == "sodium")
    assert sodium["severity"] == "low", "钠受菜库覆盖限制，只做提示"


def test_pending_meal_marks_in_progress_not_failed(db):
    """还有餐没打卡：热量没到下限只是还没吃完，不能判「没达标」"""
    tasks = _tasks(db, done=("breakfast",), pending=("lunch", "dinner"))
    ctx = _ctx(db, tasks, intake=_intake(calories=500, protein_g=20))
    result = daily_audit.audit(db, 1, TODAY, ctx)

    assert "calorie" not in _keys(result)
    assert any(i["key"] == "calorie" for i in result["pending"])
    assert "没打卡" in result["headline"], "结论要说清是还没吃完，不是没吃够"


def test_water_and_sleep_under_are_flagged(db):
    tasks = _tasks(db)
    ctx = _ctx(db, tasks, water_ml=1200, sleep_h=5.5)
    result = daily_audit.audit(db, 1, TODAY, ctx)

    assert {"water", "sleep"} <= _keys(result)
    water = next(i for i in result["failed"] if i["key"] == "water")
    assert "800" in water["gap_text"]


def test_exercise_missed_is_flagged(db):
    """运动项没打卡 → 明确说没做到，且不诱导「补练」"""
    db.add(CheckIn(user_id=1, check_date=TODAY, check_type="meal",
                   task_key="meal_breakfast", status="done"))
    db.add(CheckIn(user_id=1, check_date=TODAY, check_type="exercise",
                   task_key="exercise", status="pending"))
    db.commit()
    tasks = db.query(CheckIn).filter(CheckIn.user_id == 1).all()
    ctx = _ctx(db, tasks, burn={"exercise_kcal": 0, "exercise_min": 0, "times": 0})
    result = daily_audit.audit(db, 1, TODAY, ctx)

    exercise = next(i for i in result["failed"] if i["key"] == "exercise")
    assert "没打卡" in exercise["gap_text"]
    assert "不用补练" in exercise["now"]


def test_species_counts_canonical_names(db):
    """「蒸土豆」「土豆(熟)」「土豆」算一种，别把烹调形态算成多样"""
    for name in ("蒸土豆", "土豆(熟)", "米饭", "鸡胸肉", "西兰花"):
        db.add(FoodRecord(user_id=1, dish_name=name, calories=200, protein_g=10,
                          fiber_g=3, sodium_mg=200, sugar_g=1,
                          eaten_date=TODAY, voided_at=None))
    db.commit()
    tasks = _tasks(db)
    ctx = _ctx(db, tasks, intake=_intake())
    result = daily_audit.audit(db, 1, TODAY, ctx)

    species = next(i for i in result["failed"] if i["key"] == "species")
    assert species["actual"] == 4, "土豆三种写法只算一种"
    assert "8" in species["gap_text"]


def test_later_is_filled_from_next_plan(db):
    tasks = _tasks(db)
    ctx = _ctx(db, tasks, intake=_intake(protein_g=45))
    next_plan = {
        "headline": "后 2 天蛋白下限提到 68 g",
        "actions": [{"day": str(TODAY), "field": "diet",
                     "action": "蛋白下限提到 68 g", "reason": "日均蛋白偏低"}],
    }
    result = daily_audit.audit(db, 1, TODAY, ctx, next_plan=next_plan)

    protein = next(i for i in result["failed"] if i["key"] == "protein")
    assert protein["later"] == "蛋白下限提到 68 g"
    assert result["later_headline"] == "后 2 天蛋白下限提到 68 g"


def test_no_vitamin_item(db):
    """维生素没有可信数据，宁可空着也不做假指标"""
    tasks = _tasks(db)
    result = daily_audit.audit(db, 1, TODAY, _ctx(db, tasks))
    assert all("vitamin" not in item["key"] for item in result["items"])
