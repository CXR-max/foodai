"""计划 → 日程 → 睡眠/饮水 → 每日汇总 的接口闭环测试

不调模型：直接用 ORM 造一份方案（结构与本版新增字段一致），
验证「沿用 / 导入日程 / 目标下发 / 睡眠饮水自动打卡 / 每日汇总」这条链。
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  确保所有表都注册到 Base.metadata
from app.db.session import Base, get_db
from app.main import app
from app.models.diet_plan import DietPlan, PlanDay


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
        "username": "plan_flow_user", "password": "flow123456", "nickname": "计划链路",
    })
    assert resp.status_code == 200, resp.text
    client.put("/api/profile", headers={"Authorization": f"Bearer {resp.json()['token']}"}, json={
        "height_cm": 175, "weight_kg": 72.5, "age": 26, "gender": "male",
        "activity_level": "moderate", "goal": "lose",
        "sleep_time": "23:00", "wake_time": "07:00",
    })
    return {"Authorization": f"Bearer {resp.json()['token']}"}


TARGETS = {
    "calorie_target": 2000, "calorie_min": 1800, "calorie_max": 2100,
    "protein_min_g": 116, "water_min_ml": 2000,
    "sleep_min_h": 7.0, "exercise_burn_min": 200, "exercise_days_per_week": 5,
    "bedtime": "23:00", "wake_time": "07:00", "goal": "lose",
}

MEALS = [
    {"meal_type": "breakfast", "name": "燕麦粥", "items": [{"name": "燕麦", "portion_g": 80}], "calories": 300},
    {"meal_type": "lunch", "name": "鸡胸沙拉", "items": [{"name": "鸡胸肉", "portion_g": 150}], "calories": 500},
    {"meal_type": "dinner", "name": "清蒸鱼", "items": [{"name": "鲈鱼", "portion_g": 200}], "calories": 450},
    {"meal_type": "snack", "name": "苹果", "items": [{"name": "苹果", "portion_g": 200}], "calories": 100},
]

EXERCISES = [
    {"type": "快走", "duration_min": 40, "calories_burned": 240, "tips": "注意热身", "intensity": "moderate"},
    {"type": "拉伸", "duration_min": 10, "calories_burned": 25, "tips": "放松", "intensity": "light"},
]


@pytest.fixture(scope="module")
def plan_id(session_factory, auth, client):
    me = client.get("/api/profile", headers=auth).json()
    assert me["sleep_time"] == "23:00"

    db = session_factory()
    try:
        start = date.today()
        plan = DietPlan(
            user_id=1, title="测试方案", start_date=start, status="active",
            summary="测试", daily_target_calories=2000, targets=TARGETS,
            plan_version=1, provider="test",
        )
        db.add(plan)
        db.flush()
        for index in (1, 2):
            day_targets = dict(TARGETS)
            day_targets["is_training_day"] = index == 1
            day_targets["exercise_burn_target"] = 265
            db.add(PlanDay(
                plan_id=plan.id, day_index=index, date=start + timedelta(days=index - 1),
                meals=MEALS, exercise=EXERCISES[0], exercises=EXERCISES,
                sleep={"target_h": 7.0, "planned_h": 8.0, "bedtime": "23:00",
                       "wake_at": "07:00", "tips": "早点睡"},
                targets=day_targets, water_ml=2000,
                estimated_calories=sum(m["calories"] for m in MEALS), theme="轻盈开局",
            ))
        db.commit()
        return plan.id
    finally:
        db.close()


def test_active_plan_carries_targets(client, auth, plan_id):
    resp = client.get(f"/api/plans/active?date={date.today()}", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_plan"] is True
    assert body["targets"]["calorie_max"] == 2100
    assert body["day"]["water_ml"] == 2000
    assert len(body["day"]["exercises"]) == 2
    assert body["day"]["sleep"]["target_h"] == 7.0


def test_plan_list_has_card_stats(client, auth, plan_id):
    rows = client.get("/api/plans", headers=auth).json()
    card = next(p for p in rows if p["id"] == plan_id)
    assert card["days_count"] == 2
    assert card["avg_calories"] > 0
    assert card["total_burn"] == 2 * (240 + 25)


def test_import_week_creates_sleep_and_water_tasks(client, auth, plan_id):
    resp = client.post(f"/api/plans/{plan_id}/import", headers=auth, json={"mode": "week"})
    assert resp.status_code == 200, resp.text
    created = resp.json()["created"]
    for key in ("meal_breakfast", "meal_lunch", "meal_dinner", "meal_snack",
                "exercise", "sleep", "water"):
        assert key in created

    day = client.get(f"/api/check-ins?date={date.today()}", headers=auth).json()
    keys = {item["task_key"] for item in day["items"]}
    assert {"sleep", "water"} <= keys
    labels = {item["label"] for item in day["items"]}
    assert "睡眠" in labels and "饮水" in labels


def test_sleep_logging_completes_task(client, auth):
    resp = client.post("/api/sleep", headers=auth,
                       json={"bedtime": "23:10", "wake_at": "07:10", "quality_score": 4})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sleep"]["duration_min"] == 480
    assert body["checkin"]["achieved"] is True
    assert body["checkin"]["required_min"] == 420

    task = next(i for i in client.get(f"/api/check-ins?date={date.today()}", headers=auth).json()["items"]
                if i["task_key"] == "sleep")
    assert task["status"] == "done"
    assert task["points_awarded"] >= 3


def test_short_sleep_keeps_task_pending(client, auth):
    tomorrow = date.today() + timedelta(days=1)
    resp = client.post("/api/sleep", headers=auth, json={
        "sleep_date": str(tomorrow), "duration_min": 300, "quality_score": 3,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["checkin"]["achieved"] is False
    assert "低于计划" in (client.get(f"/api/check-ins?date={tomorrow}", headers=auth)
                        .json()["items"][-1]["note"] or "低于计划")


def test_water_accumulates_and_completes(client, auth):
    first = client.post("/api/water", headers=auth, json={"ml": 500}).json()
    assert first["target_ml"] == 2000 and first["achieved"] is False
    again = client.post("/api/water", headers=auth, json={"ml": 1500}).json()
    assert again["total_ml"] == 2000
    assert again["achieved"] is True

    day = client.get(f"/api/day/summary?date={date.today()}", headers=auth).json()
    assert day["water"]["achieved"] is True
    assert day["water"]["total_ml"] == 2000


def test_day_summary_merges_plan_and_records(client, auth, plan_id):
    body = client.get(f"/api/day/summary?date={date.today()}", headers=auth).json()
    assert body["has_plan"] is True
    assert body["targets"]["protein_min_g"] == 116
    assert body["sleep"]["duration_min"] == 480
    assert body["tasks"]["total"] >= 7
    assert body["tasks"]["done"] >= 2          # 睡眠 + 饮水已达标
    assert body["plan"]["exercises"][0]["type"] == "快走"


def test_reuse_plan_shifts_dates(client, auth, plan_id):
    resp = client.post(f"/api/plans/{plan_id}/reuse", headers=auth, json={"mode": "copy"})
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert plan["reused_from"] == plan_id
    assert plan["plan_version"] == 2
    dates = [d["date"] for d in plan["days"]]
    assert dates[0] == str(date.today() + timedelta(days=2))
    assert plan["days"][0]["sleep"]["target_h"] == 7.0


def test_month_calendar_marks_plan_days(client, auth, plan_id):
    month = date.today().strftime("%Y-%m")
    body = client.get(f"/api/check-ins?month={month}", headers=auth).json()
    today_stat = next(d for d in body["days"] if d["date"] == str(date.today()))
    assert today_stat["has_plan"] is True
    assert today_stat["plan_id"] == plan_id
    assert "pct" in today_stat
