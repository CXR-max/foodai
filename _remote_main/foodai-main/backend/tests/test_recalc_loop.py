"""连续偏离 → 重算目标（P2）与闭环自省

核心断言：连续 3 天同向偏离时，系统的动作是**纠正目标**，不是继续压摄入。
「今天没吃够，明天再少吃点」重复三天，就把一次估算误差变成了系统性低摄入。
"""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.session import Base
from app.models.diet_deviation import DayMode, DietDeviation, PlanAdjustment
from app.models.food_record import FoodRecord
from app.models.health_profile import HealthProfile
from app.models.user import User
from app.models.weight_log import WeightLog
from app.services import loop_closure, recalc

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
    u = User(username="recalc", password_hash="x", points_total=0)
    db.add(u)
    db.flush()
    db.add(HealthProfile(user_id=u.id, gender="male", age=30, weight_kg=70,
                         height_cm=175, activity_level="moderate", goal="lose",
                         daily_calorie_target=1900))
    db.commit()
    return u


def _seed_deviation(db, user_id, day, kind="over", nutrient="calorie", severity="hard"):
    db.add(DietDeviation(user_id=user_id, dev_date=day, kind=kind, nutrient=nutrient,
                         actual=2500 if kind == "over" else 1200, limit_value=2100,
                         delta=400, attribution="user", source="total",
                         severity=severity, settlement="final", status="open"))
    db.commit()


def _seed_intake(db, user_id, day, calories):
    db.add(FoodRecord(user_id=user_id, dish_name="测试餐", portion_g=500,
                      calories=calories, protein_g=80, fat_g=70, carb_g=250,
                      fiber_g=20, sodium_mg=2000, sugar_g=20,
                      meal_type="lunch", origin="plan", eaten_date=day))
    db.commit()


def _days(n: int) -> list[date]:
    """取最近 n 个「已定稿」的日子。

    不能写死 TODAY-1..-n：夜里 00:00~06:00 昨天还没定稿（settlement 规则），
    trend 只统计定稿天，写死日期会在跨夜时段踩空——按定稿状态取，任何时刻跑都稳。
    """
    from app.services import settlement
    out: list[date] = []
    day = date.today()
    while len(out) < n:
        day -= timedelta(days=1)
        if settlement.is_final(day):
            out.append(day)
    return out


def test_three_days_same_direction_triggers_trend(db, user):
    for day in _days(3):
        _seed_deviation(db, user.id, day)
    t = recalc.trend(db, user.id)
    assert t["direction"] == "over"
    assert t["streak"] == 3


def test_two_days_is_not_a_trend(db, user):
    """一次两次是偶然，不能当趋势去改目标"""
    for day in _days(2):
        _seed_deviation(db, user.id, day)
    assert recalc.trend(db, user.id)["streak"] < recalc.TREND_DAYS


def test_soft_deviations_do_not_count(db, user):
    """浮动带内的略高属于正常波动，不该触发重算"""
    for day in _days(3):
        _seed_deviation(db, user.id, day, severity="soft")
    assert recalc.trend(db, user.id)["direction"] == "none"


def test_energy_balance_estimates_real_tdee(db, user):
    """有两次体重记录时，用「平均摄入 − 体重变化×7700/天」反推，比公式准"""
    days = _days(3)
    for day in days:
        _seed_intake(db, user.id, day, 2500)
    db.add(WeightLog(user_id=user.id, weight_kg=70.0, recorded_date=min(days) - timedelta(days=1)))
    db.add(WeightLog(user_id=user.id, weight_kg=70.3, recorded_date=max(days)))
    db.commit()

    estimate = recalc.estimate_tdee(db, user.id, days)
    assert estimate["method"] == "energy_balance"
    assert estimate["tdee"] < 2500          # 体重涨了 → 消耗低于摄入
    assert estimate["weight_delta_kg"] > 0


def test_recalc_lowers_target_instead_of_pressing_intake(db, user):
    """连续吃超 + 体重上升 → 目标下调（不再压摄入），且不低于 BMR"""
    days = _days(3)
    for day in days:
        _seed_deviation(db, user.id, day)
        _seed_intake(db, user.id, day, 2600)
    db.add(WeightLog(user_id=user.id, weight_kg=70.0, recorded_date=min(days) - timedelta(days=1)))
    db.add(WeightLog(user_id=user.id, weight_kg=70.4, recorded_date=max(days)))
    db.commit()

    proposal = recalc.propose(db, user.id, now=datetime.now())
    assert proposal is not None
    assert proposal["new_target"] != proposal["old_target"]
    assert "反推" in proposal["reason"]
    assert proposal["new_target"] >= (proposal["bmr"] or 0)
    assert "不是你没做到" in proposal["message"]


def test_no_proposal_when_already_at_target(db, user):
    """没有连续偏离 → 不动目标"""
    assert recalc.propose(db, user.id) is None


# ---------------- 闭环 ----------------

def test_adoption_rate_counts_proposed_vs_applied(db, user):
    for i in range(4):
        db.add(PlanAdjustment(user_id=user.id, kind="next_days_trim", status="applied",
                              effective_date=TODAY - timedelta(days=i), scope="next_days"))
    db.add(PlanAdjustment(user_id=user.id, kind="today_exercise", status="proposed",
                          effective_date=TODAY, scope="today"))
    db.commit()

    stats = loop_closure.adoption(db, user.id)
    assert stats["applied"] == 4
    assert stats["proposed"] == 5
    assert stats["rate"] == 0.8
    assert stats["by_kind"]["next_days_trim"]["rate"] == 1.0


def test_low_adoption_is_blamed_on_the_advice(db, user):
    """采纳率低时要说「建议不实用」，而不是「用户不听话」"""
    for i in range(4):
        db.add(PlanAdjustment(user_id=user.id, kind="today_exercise", status="proposed",
                              effective_date=TODAY - timedelta(days=i), scope="today"))
    db.commit()
    stats = loop_closure.adoption(db, user.id)
    assert stats["rate"] == 0.0
    assert "不是你的问题" in stats["note"]


def test_exception_ratio_flags_over_strict_standard(db, user):
    """半个月有 8 天挂例外 → 说明口径太紧，该放宽而不是骂用户"""
    for i in range(8):
        db.add(DayMode(user_id=user.id, mode_date=TODAY - timedelta(days=i), mode="social"))
    db.commit()
    stats = loop_closure.exception_ratio(db, user.id, days=14)
    assert stats["count"] == 8
    assert stats["note"] and "太紧" in stats["note"]


def test_outcome_backfill_marks_unresolved(db, user):
    """处置后同一项还在偏 → resolved=False（手段无效，下次换一种）"""
    dev = DietDeviation(user_id=user.id, dev_date=TODAY - timedelta(days=3),
                        kind="over", nutrient="calorie", delta=400, attribution="user",
                        severity="hard", status="handled", settlement="final")
    db.add(dev)
    db.flush()
    db.add(PlanAdjustment(user_id=user.id, deviation_id=dev.id, kind="next_days_trim",
                          status="applied", effective_date=TODAY - timedelta(days=3)))
    # 处置后第二天又超了同一项
    _seed_deviation(db, user.id, TODAY - timedelta(days=2))
    db.commit()

    updated = loop_closure.backfill_outcomes(db, user.id)
    db.commit()
    assert updated == 1
    row = db.query(PlanAdjustment).filter(PlanAdjustment.kind == "next_days_trim").first()
    assert row.outcome["resolved"] is False
    assert row.outcome["same_nutrient_hits"] == 1
