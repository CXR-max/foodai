"""打卡折算摄入：点「已完成」就等于按方案吃了，指标必须立刻有数

这是用户实际反馈的问题：日程里早餐、午餐都显示「已完成」，
但「今日指标区间」全是「待记录」，因为判定只读手动记录的食物。

口径（services/diet_intake.py）：
- 手动/拍照记了 → 用记录
- 没记但已打卡、方案里这餐有菜 → 按方案菜品折算
- 两者按餐次互斥，不重复计数
"""
from datetime import date, datetime, timedelta

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

TARGETS = {
    "calorie_target": 1900, "calorie_min": 1800, "calorie_max": 2100,
    "protein_min_g": 110, "protein_max_g": 150,
    "water_min_ml": 2000, "sleep_min_h": 7.0,
    "exercise_burn_min": 200, "exercise_days_per_week": 5,
    "bedtime": "23:00", "wake_time": "07:00", "goal": "lose",
}
# 早餐实算约 604 kcal / 蛋白 29.5 g（燕麦80 + 牛奶250 + 鸡蛋100）
MEALS = [
    {"meal_type": "breakfast", "name": "早餐", "items": [
        {"name": "燕麦", "portion_g": 80}, {"name": "牛奶", "portion_g": 250},
        {"name": "鸡蛋", "portion_g": 100}]},
    {"meal_type": "lunch", "name": "午餐", "items": [
        {"name": "米饭", "portion_g": 250}, {"name": "鸡胸肉", "portion_g": 200}]},
    {"meal_type": "dinner", "name": "晚餐", "items": [
        {"name": "米饭", "portion_g": 200}, {"name": "西兰花", "portion_g": 150}]},
]

BREAKFAST_KCAL = 604   # 上界留 5 kcal 误差


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
        "username": "diet_intake_user", "password": "intake123456", "nickname": "摄入测试",
    })
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest.fixture(autouse=True)
def _clean(session_factory):
    """各用例互不影响：每次都从空白的数据开始"""
    db = session_factory()
    try:
        db.query(CheckIn).delete()
        db.query(FoodRecord).delete()
        db.query(PlanDay).delete()
        db.query(DietPlan).delete()
        db.commit()
    finally:
        db.close()
    yield


def seed_day(session_factory, *, task_status: dict[str, str] | None = None,
             plan_status: str = "active") -> dict[str, int]:
    """造一份只有今天的方案 + 三条餐食任务，返回 {任务名: checkin_id}"""
    db = session_factory()
    try:
        plan = DietPlan(user_id=1, title="摄入测试方案", start_date=date.today(),
                        status=plan_status, summary="测试", daily_target_calories=1900,
                        targets=TARGETS, provider="test", plan_version=1)
        db.add(plan)
        db.flush()
        day = PlanDay(plan_id=plan.id, day_index=1, date=date.today(),
                      meals=[dict(m) for m in MEALS], targets=dict(TARGETS), water_ml=2000)
        db.add(day)
        db.flush()

        ids: dict[str, int] = {}
        for key in ("meal_breakfast", "meal_lunch", "meal_dinner"):
            status = (task_status or {}).get(key, "pending")
            c = CheckIn(user_id=1, check_date=date.today(), check_type="diet",
                        task_key=key, status=status, plan_day_id=day.id)
            db.add(c)
            db.flush()
            ids[key] = c.id
        db.commit()
        return ids
    finally:
        db.close()


def summary(client, auth) -> dict:
    resp = client.get(f"/api/day/summary?date={date.today()}", headers=auth)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_checkin_alone_counts_plan_nutrition(session_factory, client, auth):
    """只点「早餐已完成」、什么都不记 → 摄入立刻就按方案算出来"""
    ids = seed_day(session_factory)
    before = summary(client, auth)
    assert before["diet"]["status"] == "no_data"

    resp = client.post(f"/api/check-ins/{ids['meal_breakfast']}/complete", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 打卡响应里直接带判定结果，前端不用再等一次请求
    assert body["diet"]["status"] != "no_data"
    assert body["intake"]["estimated_meals"] == ["breakfast"]

    after = summary(client, auth)
    assert after["intake"]["calories"] == pytest.approx(BREAKFAST_KCAL, abs=8)
    assert after["intake"]["protein_g"] > 20
    assert after["intake"]["estimated"] is True
    assert after["intake"]["estimated_labels"] == ["早餐"]
    assert after["diet"]["status"] != "no_data"
    assert after["diet"]["calorie"]["actual"] == pytest.approx(BREAKFAST_KCAL, abs=8)
    # 只吃了早餐、还有两餐没打卡：状态是"进行中"，不能判"热量不足"吓人
    assert after["diet"]["status"] == "in_progress"
    assert "没打卡" in after["diet"]["hint"]


def test_record_wins_over_plan_estimate(session_factory, client, auth):
    """这一餐自己记过 → 用记录，不再叠加方案，避免翻倍"""
    ids = seed_day(session_factory)
    db = session_factory()
    try:
        db.add(FoodRecord(user_id=1, dish_name="自己记的早餐", calories=500, protein_g=25,
                          meal_type="breakfast", eaten_date=date.today(),
                          eaten_at=datetime.now()))
        db.commit()
    finally:
        db.close()

    client.post(f"/api/check-ins/{ids['meal_breakfast']}/complete", headers=auth)
    after = summary(client, auth)
    assert after["intake"]["calories"] == pytest.approx(500, abs=1)
    assert after["intake"]["estimated_meals"] == []


def test_unknown_meal_type_blocks_estimate(session_factory, client, auth):
    """有记录但没写餐次 → 判断不了哪餐记过了，宁可少算也不重复算"""
    ids = seed_day(session_factory)
    db = session_factory()
    try:
        db.add(FoodRecord(user_id=1, dish_name="快速记录", calories=1200, protein_g=40,
                          meal_type=None, eaten_date=date.today(), eaten_at=datetime.now()))
        db.commit()
    finally:
        db.close()

    client.post(f"/api/check-ins/{ids['meal_breakfast']}/complete", headers=auth)
    after = summary(client, auth)
    assert after["intake"]["calories"] == pytest.approx(1200, abs=1)
    assert after["intake"]["estimated_meals"] == []


def test_pending_meal_is_not_counted(session_factory, client, auth):
    """没打卡的餐不折算：只吃了早餐，就不该显示全天吃够"""
    seed_day(session_factory)
    after = summary(client, auth)
    assert after["intake"]["calories"] == 0
    assert after["intake"]["estimated_meals"] == []


def test_checkin_counts_after_plan_archived(session_factory, client, auth):
    """今天的任务挂在「已归档」的方案上，打卡也得有数

    真实场景：用户 11-16 生成方案 A（11-16 起，正在执行），当晚又生成方案 B（11-23 起），
    导入 B 时 A 被归档，可今天的任务还挂在 A 的方案天上。
    这时「按日期 + 进行中」找方案天会扑空 → 摄入恒为 0 →
    点完「已完成」指标还是全「待记录」（用户报的正是这个）。
    打卡记录自带 plan_day_id，折算必须顺着它走。
    """
    ids = seed_day(session_factory, plan_status="archived")
    db = session_factory()
    try:
        # 另一份从下周开始的新方案，处于进行中：正好挡住"按日期查进行中方案"这条路
        later = DietPlan(user_id=1, title="下周方案", start_date=date.today(),
                         status="active", summary="测试", daily_target_calories=1900,
                         targets=TARGETS, provider="test", plan_version=1)
        db.add(later)
        db.flush()
        db.add(PlanDay(plan_id=later.id, day_index=1,
                       date=date.today() + timedelta(days=7),
                       meals=[dict(m) for m in MEALS], targets=dict(TARGETS), water_ml=2000))
        db.commit()
    finally:
        db.close()

    resp = client.post(f"/api/check-ins/{ids['meal_breakfast']}/complete", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["intake"]["calories"] > 0

    after = summary(client, auth)
    assert after["has_plan"] is True
    assert after["intake"]["calories"] == pytest.approx(BREAKFAST_KCAL, abs=8)
    assert after["intake"]["estimated_meals"] == ["breakfast"]
    assert after["diet"]["status"] == "in_progress"


def test_intake_splits_plan_and_extra(session_factory, client, auth):
    """方案内 / 方案外要分开：归因靠这个，不拆开就永远说不清是谁的问题

    点了早餐打卡（按方案折算）+ 额外喝了一杯奶茶（origin=extra）：
    - plan 只算方案那部分 → 不冤枉方案
    - extra 单独记 → 超标时归因给「自己的选择」
    """
    ids = seed_day(session_factory)
    db = session_factory()
    try:
        db.add(FoodRecord(user_id=1, dish_name="奶茶", calories=420, protein_g=4,
                          fat_g=14, carb_g=62, sugar_g=38, origin="extra",
                          meal_type="snack", eaten_date=date.today(),
                          eaten_at=datetime.now()))
        db.commit()
    finally:
        db.close()

    client.post(f"/api/check-ins/{ids['meal_breakfast']}/complete", headers=auth)
    after = summary(client, auth)
    intake = after["intake"]

    assert intake["extra"]["calories"] == pytest.approx(420, abs=1)
    assert intake["plan"]["calories"] == pytest.approx(BREAKFAST_KCAL, abs=8)
    assert intake["calories"] == pytest.approx(BREAKFAST_KCAL + 420, abs=8)
    assert intake["extra_records"] == 1
    # 额外加餐不影响「哪餐记过了」，早餐仍按方案折算
    assert intake["estimated_meals"] == ["breakfast"]


def test_plain_record_counts_as_plan(session_factory, client, auth):
    """没写归属但写了餐次的老记录 → 算方案内（那时还没有归属字段）"""
    ids = seed_day(session_factory)
    db = session_factory()
    try:
        db.add(FoodRecord(user_id=1, dish_name="自己记的早餐", calories=600,
                          protein_g=30, meal_type="breakfast", eaten_date=date.today(),
                          eaten_at=datetime.now()))
        db.commit()
    finally:
        db.close()

    client.post(f"/api/check-ins/{ids['meal_breakfast']}/complete", headers=auth)
    intake = summary(client, auth)["intake"]
    assert intake["plan"]["calories"] == pytest.approx(600, abs=1)
    assert intake["extra"]["calories"] == 0
