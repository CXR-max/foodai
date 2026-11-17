"""改菜重算 + 方案生命周期（导入即生效 / 过期顺延 / 旧方案归档）闭环测试"""
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

TARGETS = {
    "calorie_target": 2000, "calorie_min": 1800, "calorie_max": 2100,
    "protein_min_g": 116, "water_min_ml": 2000,
    "sleep_min_h": 7.0, "exercise_burn_min": 200, "exercise_days_per_week": 5,
    "bedtime": "23:00", "wake_time": "07:00", "goal": "lose",
}

MEALS = [
    {"meal_type": "breakfast", "name": "早餐", "items": [{"name": "燕麦", "portion_g": 80}], "calories": 325},
    {"meal_type": "lunch", "name": "午餐", "items": [{"name": "米饭", "portion_g": 200},
                                                    {"name": "鸡胸肉", "portion_g": 150}], "calories": 430},
    {"meal_type": "dinner", "name": "晚餐", "items": [{"name": "米饭", "portion_g": 150},
                                                      {"name": "鸡胸肉", "portion_g": 100}], "calories": 300},
]

EXERCISES = [
    {"type": "快走", "duration_min": 40, "calories_burned": 240, "tips": "热身", "intensity": "moderate"},
    {"type": "拉伸", "duration_min": 10, "calories_burned": 25, "tips": "放松", "intensity": "light"},
]

SLEEP = {"target_h": 7.0, "planned_h": 8.0, "bedtime": "23:00", "wake_at": "07:00", "tips": "早点睡"}


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
        "username": "plan_edit_user", "password": "edit123456", "nickname": "改菜用户",
    })
    assert resp.status_code == 200, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['token']}"}
    client.put("/api/profile", headers=headers, json={
        "height_cm": 175, "weight_kg": 72.5, "age": 26, "gender": "male",
        "activity_level": "moderate", "goal": "lose",
        "sleep_time": "23:00", "wake_time": "07:00",
    })
    return headers


def make_plan(session_factory, user_id=1, start=None, status="active", days=3, title="测试方案"):
    db = session_factory()
    try:
        plan = DietPlan(
            user_id=user_id, title=title, start_date=start, status=status,
            summary="测试", daily_target_calories=2000, targets=TARGETS,
            plan_version=1, provider="test",
        )
        db.add(plan)
        db.flush()
        for index in range(1, days + 1):
            day_targets = dict(TARGETS)
            db.add(PlanDay(
                plan_id=plan.id, day_index=index, date=start + timedelta(days=index - 1),
                meals=[dict(m) for m in MEALS], exercise=EXERCISES[0], exercises=EXERCISES,
                sleep=SLEEP, targets=day_targets, water_ml=2000,
                estimated_calories=sum(m["calories"] for m in MEALS), theme="轻盈开局",
            ))
        db.commit()
        return plan.id
    finally:
        db.close()


@pytest.fixture(scope="module")
def old_plan_id(session_factory, auth):
    """一份 20 天前就结束的方案，用于模拟"历史方案导入" """
    return make_plan(session_factory, start=date.today() - timedelta(days=20), title="旧方案")


@pytest.fixture(scope="module")
def other_active_id(session_factory, auth):
    """另一份进行中的方案：导入旧方案后它应该被归档"""
    return make_plan(session_factory, start=date.today(), title="另一份进行中")


# ---------------- 生命周期 ----------------

def test_import_overdue_plan_shifts_to_today(client, auth, old_plan_id, other_active_id):
    resp = client.post(f"/api/plans/{old_plan_id}/import", headers=auth, json={"mode": "week"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["shift"]["shift_days"] == 20
    assert body["shift"]["to_date"] == str(date.today())
    assert "顺延" in body["message"]

    # 任务落在今天，能打卡
    day = client.get(f"/api/check-ins?date={date.today()}", headers=auth).json()
    keys = {item["task_key"] for item in day["items"]}
    assert {"meal_breakfast", "meal_lunch", "meal_dinner", "exercise", "sleep", "water"} <= keys

    # 导入即进行中，其它 active 归档
    plans = {p["id"]: p for p in client.get("/api/plans", headers=auth).json()}
    assert plans[old_plan_id]["status"] == "active"
    assert plans[other_active_id]["status"] == "archived"
    assert plans[old_plan_id]["start_date"] == str(date.today())


def test_archived_plan_still_viewable(client, auth, other_active_id):
    resp = client.get(f"/api/plans/{other_active_id}", headers=auth)
    assert resp.status_code == 200
    assert resp.json()["days"], "历史方案仍可查看逐天明细，方便沿用"


def test_reuse_old_plan_starts_today(client, auth, old_plan_id):
    resp = client.post(f"/api/plans/{old_plan_id}/reuse", headers=auth, json={"mode": "copy"})
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    # 旧方案已被导入顺延到今天起，沿用后排在它结束之后，不会再落回过去
    assert plan["start_date"] >= str(date.today())
    assert plan["days"][0]["date"] == plan["start_date"]
    assert plan["status"] == "active"
    # 新一份排在旧方案结束之后才开始 → 旧方案这几天还在执行，不能提前归档：
    # 归档会把 11-17~11-23 变成"没有方案天"的日子，打卡后指标全算不出来
    plans = {p["id"]: p for p in client.get("/api/plans", headers=auth).json()}
    assert plans[old_plan_id]["status"] == "active"
    assert plan["start_date"] > str(date.today())
    assert plans[plan["id"]]["phase"] == "scheduled"


# ---------------- 改菜重算 ----------------

@pytest.fixture(scope="module")
def edit_plan_id(session_factory, auth):
    return make_plan(session_factory, start=date.today(), title="改菜用方案", days=2)


HEAVY_MEAL = [{"meal_type": "lunch", "items": [
    {"name": "米饭", "portion_g": 400}, {"name": "红烧肉", "portion_g": 250},
    {"name": "薯条", "portion_g": 300}]}]


def test_preview_does_not_persist(client, auth, edit_plan_id):
    before = client.get(f"/api/plans/{edit_plan_id}", headers=auth).json()["days"][0]
    resp = client.post(f"/api/plans/{edit_plan_id}/days/1/preview", headers=auth,
                       json={"meals": HEAVY_MEAL})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"]["calories"] > 2000
    assert body["status"] == "high"
    assert body["exercise_suggestion"]["added_min"] >= 5

    after = client.get(f"/api/plans/{edit_plan_id}", headers=auth).json()["days"][0]
    assert after["meals"] == before["meals"]


def test_edit_meals_recalculates_nutrition_and_warns(client, auth, edit_plan_id):
    resp = client.put(f"/api/plans/{edit_plan_id}/days/1", headers=auth, json={
        "meals": [{"meal_type": "lunch", "items": [
            {"name": "米饭", "portion_g": 400}, {"name": "红烧肉", "portion_g": 250},
            {"name": "薯条", "portion_g": 300}]}],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    nutrition = body["nutrition"]
    assert nutrition["status"] == "high"
    assert any(w["code"] == "calorie_high" for w in nutrition["warnings"])
    assert body["estimated_calories"] == round(nutrition["total"]["calories"])
    snap = body["targets"]["nutrition_snapshot"]
    assert snap["status"] == "high" and snap["calories"] == round(nutrition["total"]["calories"])
    assert body["targets"]["calorie_source"] == "manual_edit"
    # 每道菜都带了自己的营养，前端不用再算
    assert all("calories" in item for item in body["meals"][0]["items"])


def test_edit_with_portion_rebalance_returns_to_range(client, auth, edit_plan_id):
    resp = client.put(f"/api/plans/{edit_plan_id}/days/1", headers=auth,
                      json={"meals": HEAVY_MEAL, "rebalance": "portion"})
    assert resp.status_code == 200, resp.text
    nutrition = resp.json()["nutrition"]
    assert 1700 <= nutrition["total"]["calories"] <= 2110
    assert nutrition["status"] != "high"


def test_edit_with_exercise_rebalance_extends_workout(client, auth, edit_plan_id):
    resp = client.put(f"/api/plans/{edit_plan_id}/days/1", headers=auth,
                      json={"meals": HEAVY_MEAL, "rebalance": "exercise"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    added = body["nutrition"]["exercise_added"]
    assert added["added_min"] > 0
    assert body["exercises"][0]["duration_min"] == 40 + added["added_min"]


def test_unmatched_dish_is_estimated_not_zero(client, auth, edit_plan_id):
    resp = client.put(f"/api/plans/{edit_plan_id}/days/2", headers=auth, json={
        "meals": [{"meal_type": "lunch", "items": [{"name": "外婆牌神秘炒饭", "portion_g": 300}]}],
    })
    assert resp.status_code == 200, resp.text
    item = resp.json()["meals"][0]["items"][0]
    assert item["matched"] is False
    assert item["calories"] > 0


def test_removing_meal_cleans_pending_checkin(client, auth, edit_plan_id):
    # 先恢复成三餐，导入日程后删晚餐，验证 pending 任务被清理
    client.put(f"/api/plans/{edit_plan_id}/days/1", headers=auth, json={
        "meals": [dict(m) for m in MEALS],
    })
    client.post(f"/api/plans/{edit_plan_id}/import", headers=auth, json={"mode": "week"})
    today_items = client.get(f"/api/check-ins?date={date.today()}", headers=auth).json()["items"]
    assert any(i["task_key"] == "meal_dinner" for i in today_items)

    client.put(f"/api/plans/{edit_plan_id}/days/1", headers=auth, json={
        "meals": [{"meal_type": "lunch", "items": [{"name": "米饭", "portion_g": 200}]}],
    })
    keys = {i["task_key"] for i in
            client.get(f"/api/check-ins?date={date.today()}", headers=auth).json()["items"]}
    assert "meal_dinner" not in keys
    assert "meal_lunch" in keys


# ---------------- 月历简约计划 / 换菜 ----------------

def test_month_calendar_carries_task_chips(client, auth, edit_plan_id):
    month = date.today().strftime("%Y-%m")
    body = client.get(f"/api/check-ins?month={month}", headers=auth).json()
    today_stat = next(d for d in body["days"] if d["date"] == str(date.today()))
    assert today_stat["tasks"], "月历每天要带任务摘要，才能显示简约打卡计划"
    order = ["meal_breakfast", "meal_lunch", "meal_dinner", "meal_snack",
             "exercise", "water", "sleep"]
    keys = [t["key"] for t in today_stat["tasks"]]
    assert keys == sorted(keys, key=lambda k: order.index(k) if k in order else 9)
    assert all(t["label"] and t["status"] for t in today_stat["tasks"])


def test_food_alternatives(client, auth):
    resp = client.get("/api/foods/alternatives", headers=auth,
                      params={"name": "米饭", "meal_type": "lunch"})
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert rows and all(r["name"] != "米饭" for r in rows)
    assert all(r["portion_calories"] > 0 for r in rows)


# ---------------- 历史方案重新启用提示 ----------------

def test_reimporting_history_plan_marks_reactivated(session_factory, client, auth):
    pid = make_plan(session_factory, start=date.today() - timedelta(days=10),
                    status="archived", title="曾归档方案", days=2)
    resp = client.post(f"/api/plans/{pid}/import", headers=auth, json={"mode": "week"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["activated"]["reactivated"] is True
    assert "已重新启用" in body["message"], "导入历史方案要明确告知已重新启用"

    card = {p["id"]: p for p in client.get("/api/plans", headers=auth).json()}[pid]
    assert card["status"] == "active"
    assert card["reactivated_at"]
    assert card["reactivate_count"] == 1
    assert card["original_start_date"] == str(date.today() - timedelta(days=10))

    # 沿用出的新方案从明天起才生效 → 这一份仍在自己那几天里执行，不归档，
    # 「曾重新启用」的痕迹也因此还留在卡片上
    client.post(f"/api/plans/{pid}/reuse", headers=auth, json={"mode": "copy"})
    card = {p["id"]: p for p in client.get("/api/plans", headers=auth).json()}[pid]
    assert card["status"] == "active"
    assert card["reactivated_at"]


def test_new_plan_is_not_marked_reactivated(session_factory, client, auth):
    pid = make_plan(session_factory, start=date.today(), status="active", title="新方案", days=2)
    card = {p["id"]: p for p in client.get("/api/plans", headers=auth).json()}[pid]
    assert card["reactivated_at"] is None
    assert card["reactivate_count"] == 0


# ---------------- 超限处理偏好 ----------------

def test_over_limit_policy_saved_in_profile(client, auth):
    resp = client.put("/api/profile", headers=auth, json={"over_limit_policy": "exercise"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["over_limit_policy"] == "exercise"

    profile = client.get("/api/profile", headers=auth).json()
    assert profile["over_limit_policy"] == "exercise"
    assert profile["height_cm"] == 175, "只改偏好不应冲掉身体数据"

    bad = client.put("/api/profile", headers=auth, json={"over_limit_policy": "auto"})
    assert bad.status_code == 422, "只允许 ask / portion / exercise"

    client.put("/api/profile", headers=auth, json={"over_limit_policy": "ask"})
    assert client.get("/api/profile", headers=auth).json()["over_limit_policy"] == "ask"
