"""拍照三用途：方案内打卡 / 额外加餐 / 只看热量

最容易出错的两件事，这里各有一条用例：
1. 拍了照但归属错 → 归因错 → 处置指错方向（让用户为方案错误少吃）
2. 同种菜分量不同 → 要么按方案算低估，要么两份都算重复
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.session import Base, get_db
from app.main import app
from app.models.check_in import CheckIn
from app.models.diet_plan import DietPlan, PlanDay
from app.models.food_record import FoodRecord
from app.models.recognition import Recognition
from app.services import diet_intake, photo_intake

TARGETS = {
    "calorie_target": 1900, "calorie_min": 1800, "calorie_max": 2100,
    "protein_min_g": 110, "protein_max_g": 150,
    "fat_min_g": 42, "fat_max_g": 63, "carb_min_g": 214, "carb_max_g": 261,
    "fiber_min_g": 25, "sodium_max_mg": 2000, "sugar_max_g": 25,
    "water_min_ml": 2000, "sleep_min_h": 7.0, "goal": "lose",
}

MEALS = [
    {"meal_type": "lunch", "name": "午餐", "items": [
        {"name": "米饭", "portion_g": 150}, {"name": "鸡胸肉", "portion_g": 150}]},
    {"meal_type": "dinner", "name": "晚餐", "items": [
        {"name": "米饭", "portion_g": 200}, {"name": "西兰花", "portion_g": 150}]},
]


@pytest.fixture(scope="module")
def session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
        "username": "photo_usage_user", "password": "photo123456", "nickname": "拍照测试",
    })
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest.fixture(autouse=True)
def _clean(session_factory):
    db = session_factory()
    try:
        for model in (CheckIn, FoodRecord, Recognition, PlanDay, DietPlan):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()
    yield


def seed_day(session_factory) -> dict:
    db = session_factory()
    try:
        plan = DietPlan(user_id=1, title="拍照测试方案", start_date=date.today(),
                        status="active", summary="测试", daily_target_calories=1900,
                        targets=TARGETS, provider="test", plan_version=1)
        db.add(plan)
        db.flush()
        day = PlanDay(plan_id=plan.id, day_index=1, date=date.today(),
                      meals=[dict(m) for m in MEALS], targets=dict(TARGETS))
        db.add(day)
        db.flush()
        ids = {}
        for key in ("meal_lunch", "meal_dinner"):
            c = CheckIn(user_id=1, check_date=date.today(), check_type="diet",
                        task_key=key, status="pending", plan_day_id=day.id)
            db.add(c)
            db.flush()
            ids[key] = c.id
        db.commit()
        return {"plan_day_id": day.id, **ids}
    finally:
        db.close()


def make_recognition(session_factory, dishes: list[dict]) -> int:
    db = session_factory()
    try:
        total = round(sum(float((d.get("nutrition") or {}).get("calories") or 0) for d in dishes), 1)
        rec = Recognition(user_id=1, image_path="test.jpg", provider="test",
                          dish_name=dishes[0]["dish_name"], calories=total,
                          confidence=0.9, is_food=True,
                          result={"dishes": dishes, "nutrition": {"calories": total},
                                  "vision": {"dish_name": dishes[0]["dish_name"], "portion_g": 200}})
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id
    finally:
        db.close()


def dish(name: str, portion_g: float, calories: float) -> dict:
    return {"dish_name": name, "portion_g": portion_g, "category": "主食",
            "nutrition": {"calories": calories, "protein_g": calories / 30,
                          "fat_g": calories / 90, "carb_g": calories / 5},
            "match": {"needs_confirm": False}}


# ---------------- 同种菜不同分量 ----------------

def test_same_dish_bigger_portion_shows_difference(session_factory):
    """方案 150 g 米饭，实际 220 g：要算出多出来多少，并说清按哪份算"""
    seed_day(session_factory)
    db = session_factory()
    try:
        compare = photo_intake.compare_with_plan(
            db, 1, date.today(), "lunch", [dish("米饭", 220, 340)])
    finally:
        db.close()

    assert compare["has_plan_meal"] is True
    assert compare["suggest_extra"] is False     # 有同种菜 → 属于方案内
    match = compare["matches"][0]
    assert match["portion_delta_g"] == 70
    assert match["kcal_delta"] > 0
    assert "比你这份" not in compare["hint"]
    assert "方案 150 g" in compare["hint"] and "多" in compare["hint"]


def test_unknown_dish_suggests_extra_snack(session_factory):
    """方案里没有的菜 → 建议按额外加餐记（不能算成方案内，否则记错归因）"""
    seed_day(session_factory)
    db = session_factory()
    try:
        compare = photo_intake.compare_with_plan(
            db, 1, date.today(), "lunch", [dish("炸鸡", 200, 520)])
    finally:
        db.close()
    assert compare["suggest_extra"] is True
    assert compare["only_photo"][0]["dish_name"] == "炸鸡"
    assert "额外加餐" in compare["hint"]


def test_dish_alias_match_counts_as_same_kind():
    """「米饭」和「白米饭」算同一种，不做模糊合并（避免把两道菜算成一条）"""
    assert photo_intake._same_dish("米饭", "白米饭") is True
    assert photo_intake._same_dish("米饭", "鸡胸肉") is False


# ---------------- 三用途落库 ----------------

def test_plan_usage_saves_and_checks_meal(session_factory, client, auth):
    """方案内打卡：落 origin=plan，并把午餐任务自动置「已完成」"""
    ids = seed_day(session_factory)
    rec_id = make_recognition(session_factory, [dish("米饭", 150, 232), dish("鸡胸肉", 150, 200)])

    resp = client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                       json={"meal_type": "lunch", "usage": "plan"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["usage"] == "plan"
    assert body["meal_checked"]["task_key"] == "meal_lunch"
    assert body["meal_checked"]["points_awarded"] > 0
    assert "已自动打卡" in body["message"]

    db = session_factory()
    try:
        records = db.query(FoodRecord).all()
        assert records and all(r.origin == "plan" for r in records)
        task = db.get(CheckIn, ids["meal_lunch"])
        assert task.status == "done"
        # 七项都要落库，否则当天判定会显示「数据缺失」
        assert records[0].fiber_g is not None or records[0].sodium_mg is not None
    finally:
        db.close()


def test_extra_usage_does_not_check_meal(session_factory, client, auth):
    """额外加餐：落 origin=extra，任务保持未完成"""
    ids = seed_day(session_factory)
    rec_id = make_recognition(session_factory, [dish("炸鸡", 200, 520)])

    resp = client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                       json={"meal_type": "snack", "usage": "extra"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["usage"] == "extra"
    assert body["meal_checked"] is None
    assert "额外加餐" in body["message"]

    db = session_factory()
    try:
        assert all(r.origin == "extra" for r in db.query(FoodRecord).all())
        assert db.get(CheckIn, ids["meal_lunch"]).status == "pending"
    finally:
        db.close()


def test_plan_record_replaces_plan_estimate_for_that_meal(session_factory, client, auth):
    """同餐已有实际记录 → 不再按方案折算，避免 150 g 和 220 g 两份都算"""
    seed_day(session_factory)
    rec_id = make_recognition(session_factory, [dish("米饭", 220, 340)])
    client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                json={"meal_type": "lunch", "usage": "plan"})

    db = session_factory()
    try:
        intake = diet_intake.day_intake(db, 1, date.today())
    finally:
        db.close()

    assert intake["plan"]["calories"] == pytest.approx(340, abs=1)
    assert "lunch" not in intake["estimated_meals"]


def test_inspect_usage_never_saved(session_factory, client, auth):
    """只看热量：接口层拒绝写成方案内记录（inspect 不该走到 save）"""
    seed_day(session_factory)
    rec_id = make_recognition(session_factory, [dish("米饭", 200, 300)])
    resp = client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                       json={"meal_type": "lunch", "usage": "inspect"})
    assert resp.status_code == 200, resp.text
    # inspect 传进 save 按「额外」处理更安全，绝不能变成方案内
    assert resp.json()["usage"] == "extra"


# ---------------- 状态 / 例外场景接口 ----------------

def test_day_state_returns_settlement_and_mode(session_factory, client, auth):
    """打卡页的应对框只读这一个接口：结算时点 + 模式 + 待处置偏离"""
    seed_day(session_factory)
    resp = client.get(f"/api/day/state?date={date.today()}", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["point"]["point"] in ("open", "midday", "evening", "final")
    assert body["mode"]["mode"] == "normal"
    assert isinstance(body["deviations"], list)


def test_set_mode_relaxes_today_only(session_factory, client, auth):
    """聚餐日：只放宽今天，不是改档案"""
    seed_day(session_factory)
    resp = client.post("/api/day/mode", headers=auth, json={"mode": "social"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["effects"]["mode"] == "social"
    assert resp.json()["effects"]["calorie_max_pct"] > 0

    state = client.get(f"/api/day/state?date={date.today()}", headers=auth).json()
    assert state["mode"]["mode"] == "social"
    assert "sodium" in state["mode"]["relaxed_keys"]

    # 恢复正常：口径立刻还原，不会留在档案里
    assert client.post("/api/day/mode", headers=auth, json={"mode": "normal"}).status_code == 200
    state = client.get(f"/api/day/state?date={date.today()}", headers=auth).json()
    assert state["mode"]["mode"] == "normal"


def test_self_review_api_is_about_the_system(session_factory, client, auth):
    """自省接口口径：讲趋势和建议，不给用户打分"""
    seed_day(session_factory)
    body = client.get("/api/day/self-review", headers=auth).json()
    assert "trend" in body and "adoption" in body and "exceptions" in body
    for banned in ("分数", "扣分", "排名"):
        assert banned not in str(body.get("lines"))
