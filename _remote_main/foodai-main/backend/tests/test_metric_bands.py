"""每日打卡指标区间（services/metric_bands.py）的口径测试

要守住的三件事：
1. 每个指标有确切的 min / max，浮动容忍带按比例算，不是拍脑袋的魔法数
2. 判定分五档：区间内 / 略低略高（算达标）/ 明显偏离（不算）
3. 训练日热量上限按额外消耗的 40% 回补，恢复日不动
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  确保所有表都注册到 Base.metadata
from app.db.session import Base, get_db
from app.main import app
from app.services import metric_bands, plan_targets


@pytest.fixture(scope="module")
def session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestingSession
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def client(session_factory):
    return TestClient(app)


@pytest.fixture(scope="module")
def auth(client):
    resp = client.post("/api/auth/register", json={
        "username": "band_user", "password": "band123456", "nickname": "区间口径",
    })
    assert resp.status_code == 200, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['token']}"}
    client.put("/api/profile", headers=headers, json={
        "height_cm": 175, "weight_kg": 70, "age": 24, "gender": "male",
        "activity_level": "moderate", "goal": "lose",
        "sleep_time": "23:00", "wake_time": "07:00",
    })
    return headers


def _targets(**over):
    t = plan_targets.build_targets({
        "height_cm": 175, "weight_kg": 70, "age": 24, "gender": "male",
        "activity_level": "moderate", "goal": "lose",
    })
    t.update(over)
    return t


def test_targets_have_both_bounds():
    t = _targets()
    assert t["calorie_min"] < t["calorie_max"]
    assert t["protein_min_g"] < t["protein_max_g"]
    assert t["water_min_ml"] < t["water_max_ml"] <= 4000
    assert t["sleep_min_h"] < t["sleep_max_h"]
    assert t["exercise_burn_min"] < t["exercise_burn_max"]
    assert t["steps_min"] < t["steps_max"]


def test_band_status_five_levels():
    t = _targets(calorie_min=1800, calorie_max=2100)
    band = metric_bands.band_for(t, "calorie")
    assert (band["min"], band["max"]) == (1800, 2100)

    assert metric_bands.build_band(t, "calorie", 1950)["status"] == "in_range"
    assert metric_bands.build_band(t, "calorie", 1750)["status"] == "low"      # 浮动带内
    assert metric_bands.build_band(t, "calorie", 2160)["status"] == "high"
    assert metric_bands.build_band(t, "calorie", 1500)["status"] == "under"
    assert metric_bands.build_band(t, "calorie", 2600)["status"] == "over"
    assert metric_bands.build_band(t, "calorie", None)["status"] == "no_data"


def test_soft_band_counts_as_achieved():
    t = _targets(sleep_min_h=7.0, sleep_max_h=9.0)
    # 6.6h：低于 7h 但在 0.5h 容忍内 → 算达标
    ok, band = metric_bands.achieved(t, "sleep", 6.6)
    assert ok and band["status"] == "low"
    # 6.3h：掉出容忍带 → 不算
    ok, band = metric_bands.achieved(t, "sleep", 6.3)
    assert not ok and band["status"] == "under"


def test_water_soft_min_is_90_percent():
    t = _targets(water_min_ml=2000, water_max_ml=2800)
    band = metric_bands.band_for(t, "water")
    assert band["soft_min"] == 1800
    assert metric_bands.achieved(t, "water", 1850)[0] is True
    assert metric_bands.achieved(t, "water", 1500)[0] is False
    # 喝太多也提示，但仍在容忍内算达标；超出容忍带才算超标
    over_band = metric_bands.build_band(t, "water", 3400)
    assert over_band["status"] == "high"
    assert over_band["factor"] == 0.75
    too_much = metric_bands.build_band(t, "water", 4000)
    assert too_much["status"] == "over"
    assert "电解质" in too_much["hint"]


def test_training_day_raises_calorie_ceiling():
    t = _targets()
    t.update({"calorie_max": 2100, "is_training_day": True, "exercise_burn_target": 300,
              "exercise_burn_min": 200})
    eff = metric_bands.effective_calorie_band(t)
    assert eff["calorie_max"] == 2220          # 300 × 40% = 120
    assert "训练" in eff["calorie_band_note"]

    rest = dict(t, is_training_day=False)
    assert metric_bands.effective_calorie_band(rest)["calorie_max"] == 2100


def test_build_bands_skips_missing_bounds():
    bands = metric_bands.build_bands({"calorie_min": 1800, "calorie_max": 2100}, {})
    keys = [b["key"] for b in bands]
    assert keys == ["calorie"]
    assert bands[0]["status"] == "no_data"


def test_day_summary_exposes_bands(client, auth):
    resp = client.get(f"/api/day/summary?date={date.today()}", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bands"], "每日汇总要给出指标区间"
    calorie = next(b for b in body["bands"] if b["key"] == "calorie")
    assert calorie["min"] and calorie["max"] and calorie["range_text"]
    assert calorie["status"] in ("in_range", "low", "high", "under", "over", "no_data")
    # 没有方案时也要有目标（按档案兜底），否则日程顶部是空的
    assert body["targets"]["calorie_max"]


def test_sleep_checkin_uses_band_tolerance(client, auth):
    day = date.today()
    resp = client.post("/api/sleep", headers=auth, json={
        "sleep_date": str(day), "bedtime": "23:30", "wake_at": "06:10",   # 6h40m
    })
    assert resp.status_code == 200, resp.text
    result = resp.json()["checkin"]
    assert result["band"]["status"] in ("in_range", "low")
    assert result["achieved"] is True, "6h40m 落在 7h 的 0.5h 容忍内，应算达标"
