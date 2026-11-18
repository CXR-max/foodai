"""超限处置：三时点 / 分时窗口 / 五条安全底线

这些用例断言的是「正常逻辑」而不是代码行为，所以每条都写成"什么场景下不该说什么话"：
- 深夜不许劝人加练（影响睡眠，得不偿失）
- 多余 200 kcal 不许往后面摊（估算误差本来就这么大）
- 额外加餐默认不处置（用户的选择，不指责）
- 方案内超标不许让用户少吃（方案的问题）
- 生病当天不许推任何处置
"""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.session import Base
from app.models.check_in import CheckIn
from app.models.diet_deviation import DietDeviation
from app.models.user import User
from app.services import day_mode, disposition, metric_bands, settlement

TARGETS = {
    "calorie_target": 1900, "calorie_min": 1800, "calorie_max": 2100,
    "protein_min_g": 110, "protein_max_g": 150,
    "fat_min_g": 42, "fat_max_g": 63, "carb_min_g": 214, "carb_max_g": 261,
    "fiber_min_g": 25, "sodium_max_mg": 2000, "sugar_max_g": 25,
    "exercise_burn_max": 500, "weight_kg": 65,
    "prescription": {"bmr": 1500, "tdee": 2300, "deficit_pct": 0.15},
}

TODAY = date.today()


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


@pytest.fixture()
def user(db):
    u = User(username="tester", password_hash="x", points_total=0)
    db.add(u)
    db.commit()
    return u


def _dev(kind="over", nutrient="calorie", delta=420, attribution="user", day=TODAY):
    return DietDeviation(user_id=1, dev_date=day, kind=kind, nutrient=nutrient,
                         delta=delta, attribution=attribution, status="open",
                         actual=2500, limit_value=2100)


def _pending_meals(db, user_id, day, meals=("dinner",)):
    for meal in meals:
        db.add(CheckIn(user_id=user_id, check_date=day, check_type="meal",
                       task_key=f"meal_{meal}", status="pending"))
    db.commit()


# ---------------- 三时点 ----------------

def test_settlement_morning_does_not_judge_under():
    """上午 10 点：还没吃完，不许判「不足」"""
    point = settlement.settlement_of(TODAY, datetime.combine(TODAY, datetime.min.time()).replace(hour=10))
    assert point["point"] == "open"
    assert point["settled"] is False
    assert point["judge_under"] is False


def test_settlement_midday_only_judges_over():
    """13:30：只判吃超，不判不足"""
    point = settlement.settlement_of(TODAY, datetime.combine(TODAY, datetime.min.time()).replace(hour=13, minute=30))
    assert point["point"] == "midday"
    assert point["settled"] is True
    assert point["judge_under"] is False


def test_settlement_after_2130_no_more_adjust_today():
    """21:30 之后：今天不再加练，也不再补吃"""
    point = settlement.settlement_of(TODAY, datetime.combine(TODAY, datetime.min.time()).replace(hour=22))
    assert point["point"] == "evening"
    assert point["can_adjust_today"] is False


def test_settlement_yesterday_morning_still_open_window():
    """昨天的事，今天凌晨 5 点还能调整今天起的安排；7 点就定稿"""
    yesterday = TODAY - timedelta(days=1)
    early = settlement.settlement_of(yesterday, datetime.combine(TODAY, datetime.min.time()).replace(hour=5))
    assert early["point"] == "evening"
    late = settlement.settlement_of(yesterday, datetime.combine(TODAY, datetime.min.time()).replace(hour=7))
    assert late["point"] == "final"


def test_future_day_is_pending():
    point = settlement.settlement_of(TODAY + timedelta(days=2))
    assert point["point"] == "pending"
    assert point["settled"] is False


# ---------------- 分时窗口 ----------------

def test_window_follows_clock():
    at = lambda h, m=0: datetime.combine(TODAY, datetime.min.time()).replace(hour=h, minute=m)  # noqa: E731
    assert disposition.window_for(at(8)) == "morning"
    assert disposition.window_for(at(12)) == "midday"
    assert disposition.window_for(at(17)) == "afternoon"
    assert disposition.window_for(at(20)) == "evening"
    assert disposition.window_for(at(23)) == "late"
    assert disposition.window_for(at(10), is_today=False) == "past"


# ---------------- 五条安全底线 ----------------

def test_late_night_never_suggests_exercise(db, user):
    """S1：21:30 之后不劝加练——影响睡眠的代价大于一顿饭"""
    _pending_meals(db, user.id, TODAY, ("snack",))
    opts = disposition.options(db, user.id, TODAY, _dev(), TARGETS,
                               now=datetime.combine(TODAY, datetime.min.time()).replace(hour=22, minute=30))
    exercise = [o for o in opts if o["kind"] == "today_exercise"]
    assert exercise, "深夜也应该说明为什么不建议加练"
    assert exercise[0]["safe"] is False
    assert exercise[0]["payload"]["minutes"] == 0
    assert "S1" in " ".join(exercise[0]["safety"])


def test_exercise_covers_only_half_and_respects_cap(db, user):
    """S1：加练只抵一半，且不超当日运动额度"""
    _pending_meals(db, user.id, TODAY, ("dinner",))
    opts = disposition.options(db, user.id, TODAY, _dev(delta=600), TARGETS,
                               now=datetime.combine(TODAY, datetime.min.time()).replace(hour=15))
    exercise = [o for o in opts if o["kind"] == "today_exercise"][0]
    assert exercise["payload"]["calories"] <= 300 + 1
    assert exercise["payload"]["minutes"] > 0


def test_small_gap_is_not_carried_over(db, user):
    """S3：只超 200 kcal，不往后面摊，直接说「明天照常」"""
    _pending_meals(db, user.id, TODAY, ("dinner",))
    opts = disposition.options(db, user.id, TODAY, _dev(delta=200), TARGETS,
                               now=datetime.combine(TODAY, datetime.min.time()).replace(hour=15))
    kinds = {o["kind"] for o in opts}
    assert "next_days_trim" not in kinds
    no_action = [o for o in opts if o["kind"] == "no_action"][0]
    assert no_action["recommended"] is True
    assert "300" in (no_action["reason"] + no_action["detail"])


def test_trim_has_floor_and_ten_percent_limit(db, user):
    """S2：下调有地板（max(BMR, TDEE×0.8)），单次不超过 10%"""
    _pending_meals(db, user.id, TODAY, ())
    opts = disposition.options(db, user.id, TODAY, _dev(delta=2000), TARGETS,
                               now=datetime.combine(TODAY, datetime.min.time()).replace(hour=15))
    trim = [o for o in opts if o["kind"] == "next_days_trim"][0]
    # 目标 1900 × 10% = 190 是单日上限
    assert trim["payload"]["per_day"] <= 190


def test_extra_over_defaults_to_no_action(db, user):
    """S5：额外加餐是主动选择，默认「就这样，明天照常」"""
    opts = disposition.options(db, user.id, TODAY,
                               _dev(attribution="extra"), TARGETS,
                               now=datetime.combine(TODAY, datetime.min.time()).replace(hour=20))
    assert opts[0]["kind"] == "no_action"
    assert opts[0]["recommended"] is True
    for word in ("不该", "自律", "管住"):
        assert word not in opts[0]["detail"]


def test_plan_over_is_not_charged_to_user(db, user):
    """方案内超标：不处置，进修复队列"""
    opts = disposition.options(db, user.id, TODAY, _dev(attribution="plan"), TARGETS)
    assert [o["kind"] for o in opts] == ["fix_plan"]
    assert "不是你的问题" in opts[0]["detail"]


def test_sick_day_gives_no_disposition(db, user):
    """生病当天：只让吃和喝水，不推任何处置"""
    day_mode.set_mode(db, user.id, TODAY, "sick")
    db.commit()
    opts = disposition.options(db, user.id, TODAY, _dev(), TARGETS)
    assert len(opts) == 1
    assert opts[0]["kind"] == "no_action"
    assert "不推处置" in opts[0]["detail"]


def test_under_asks_reason_and_offers_meal_boost(db, user):
    """没吃够：先问「吃不下还是没时间」，同时给「加在没吃的那餐里」"""
    _pending_meals(db, user.id, TODAY, ("dinner",))
    opts = disposition.options(db, user.id, TODAY,
                               _dev(kind="under", nutrient="protein", delta=30, attribution="intake"),
                               TARGETS,
                               now=datetime.combine(TODAY, datetime.min.time()).replace(hour=15))
    kinds = {o["kind"] for o in opts}
    assert "ask_reason" in kinds
    assert "next_meal_boost" in kinds
    assert "S4" in " ".join(sum([o["safety"] for o in opts], []))


# ---------------- 例外场景 ----------------

def test_social_mode_relaxes_but_has_ceiling():
    """聚餐放宽 15%，但绝不超过维持热量"""
    target = dict(TARGETS)
    eff = day_mode.spec_for("social")
    bumped = day_mode.apply_to_targets(target, {**eff, "label": "聚餐"}, tdee=2300)
    assert bumped["calorie_max"] == 2300     # 2100×1.15=2415 被 TDEE 压回
    assert "已到维持热量" in bumped["day_mode_note"]


def test_travel_mode_keeps_calorie_but_relaxes_fiber():
    """出差：纤维/钠不判（外食难控），热量和蛋白照看"""
    eff = day_mode.spec_for("travel")
    assert "fiber" in eff["relaxed_keys"]
    assert "protein" not in eff["relaxed_keys"]


def test_sick_mode_pauses_scoring_in_summary():
    """生病：不给评分系数，超了也不判红"""
    intake = {
        "calories": 1400, "protein_g": 60, "fat_g": 40, "carb_g": 150,
        "fiber_g": 12, "sodium_mg": 1200, "sugar_g": 10,
        "records": 2, "plan": {}, "extra": {},
    }
    effect = {**day_mode.spec_for("sick"), "mode": "sick", "label": "生病",
              "note_text": day_mode.MODE_SPECS["sick"]["note"]}
    diet = metric_bands.diet_summary(TARGETS, intake=intake, mode_effect=effect)
    assert diet["factor"] is None
    assert diet["mode"]["pause_scoring"] is True
    assert "不评等级" in diet["hint"]


def test_relaxed_nutrient_is_not_counted_as_over():
    """聚餐日钠超标不算超标，但要留一句解释"""
    intake = {
        "calories": 2050, "protein_g": 100, "fat_g": 60, "carb_g": 220,
        "fiber_g": 26, "sodium_mg": 3200, "sugar_g": 20,
        "records": 3, "plan": {}, "extra": {},
    }
    effect = {**day_mode.spec_for("social"), "mode": "social", "label": "聚餐"}
    diet = metric_bands.diet_summary(TARGETS, intake=intake, mode_effect=effect)
    assert diet["status"] != "protein_over"
    assert "钠" in diet["hint"] and "不计超标" in diet["hint"]
