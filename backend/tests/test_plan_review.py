"""方案执行评价：打卡完成率 / 目标达成 / 连续坚持 / 调整质量，停用后留进历史方案"""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  确保所有表都注册到 Base.metadata
from app.db.session import Base, get_db
from app.main import app
from app.models.check_in import CheckIn
from app.models.diet_plan import DietPlan, PlanDay
from app.models.food_record import FoodRecord
from app.models.sleep_log import SleepLog
from app.models.water_log import WaterLog

TARGETS = {
    "calorie_target": 2000, "calorie_min": 1800, "calorie_max": 2100,
    "protein_min_g": 116, "water_min_ml": 2000,
    "sleep_min_h": 7.0, "exercise_burn_min": 200, "exercise_days_per_week": 5,
    "bedtime": "23:00", "wake_time": "07:00", "goal": "lose",
}
MEALS = [
    {"meal_type": "breakfast", "name": "早餐", "items": [
        {"name": "燕麦", "portion_g": 80}, {"name": "牛奶", "portion_g": 250},
        {"name": "鸡蛋", "portion_g": 100}], "calories": 604},
    {"meal_type": "lunch", "name": "午餐", "items": [
        {"name": "米饭", "portion_g": 250}, {"name": "鸡胸肉", "portion_g": 200},
        {"name": "西兰花", "portion_g": 200}], "calories": 628},
    {"meal_type": "dinner", "name": "晚餐", "items": [
        {"name": "米饭", "portion_g": 200}, {"name": "牛肉", "portion_g": 200},
        {"name": "西兰花", "portion_g": 150}, {"name": "红薯", "portion_g": 200}], "calories": 614},
]
# 这套餐按营养库实算约 1846 kcal / 蛋白 131 g，正好落在 TARGETS 的 1800~2100 / 116~145 内。
# 原来写的是燕麦80+米饭200+米饭150（实算 731 kcal / 蛋白 18 g），
# 一打卡就按方案折算后必然判「吃太少」，"执行到位"的用例会假性低分。
EXERCISES = [{"type": "快走", "duration_min": 40, "calories_burned": 240, "tips": "", "intensity": "moderate"}]
SLEEP = {"target_h": 7.0, "planned_h": 8.0, "bedtime": "23:00", "wake_at": "07:00"}
TASKS = {"meal_breakfast": "diet", "meal_lunch": "diet", "meal_dinner": "diet",
         "exercise": "exercise", "sleep": "sleep", "water": "water"}


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
        "username": "plan_review_user", "password": "review123456", "nickname": "评价用户",
    })
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def seed_plan(session_factory, *, start: date, days: int = 3, status: str = "archived",
              title: str = "待评价方案", import_tasks: bool = True,
              done_ratio: float = 1.0, records: bool = True, edit_count: int = 0,
              edit_status: str = "ok") -> int:
    db = session_factory()
    try:
        plan = DietPlan(user_id=1, title=title, start_date=start, status=status,
                        summary="评价测试", daily_target_calories=2000, targets=TARGETS,
                        provider="test", plan_version=1)
        db.add(plan)
        db.flush()
        plan_days = []
        for i in range(1, days + 1):
            d = start + timedelta(days=i - 1)
            day_targets = dict(TARGETS)
            if edit_count:
                day_targets["nutrition_snapshot"] = {"calories": 2050, "status": edit_status}
            day = PlanDay(plan_id=plan.id, day_index=i, date=d, meals=[dict(m) for m in MEALS],
                          exercise=EXERCISES[0], exercises=EXERCISES, sleep=SLEEP,
                          targets=day_targets, water_ml=2000, estimated_calories=1055,
                          theme="轻盈开局", edit_count=edit_count,
                          last_edited_at=datetime.now() if edit_count else None)
            db.add(day)
            db.flush()
            plan_days.append(day)

            if import_tasks:
                keys = list(TASKS)
                done_count = round(len(keys) * done_ratio)
                for idx, key in enumerate(keys):
                    db.add(CheckIn(user_id=1, check_date=d, check_type=TASKS[key], task_key=key,
                                   status="done" if idx < done_count else "pending",
                                   plan_day_id=day.id,
                                   calories_burned=240 if key == "exercise" and idx < done_count else None))

            if records:
                # 多个测试方案日期可能重叠：同一天只留一份记录（sleep_logs 一天一条）
                if not db.query(FoodRecord).filter_by(user_id=1, eaten_date=d).first():
                    db.add(FoodRecord(user_id=1, dish_name="测试餐", calories=2050, eaten_date=d,
                                      eaten_at=datetime.combine(d, datetime.min.time())))
                if not db.query(SleepLog).filter_by(user_id=1, sleep_date=d).first():
                    db.add(SleepLog(user_id=1, sleep_date=d, duration_min=450, source="manual"))
                if not db.query(WaterLog).filter_by(user_id=1, log_date=d).first():
                    db.add(WaterLog(user_id=1, log_date=d, ml=2200, source="manual"))
        db.commit()
        return plan.id
    finally:
        db.close()


# ---------------- 评价计算 ----------------

def test_review_of_well_executed_plan(session_factory, client, auth):
    pid = seed_plan(session_factory, start=date.today() - timedelta(days=3),
                    title="执行到位方案", done_ratio=1.0, edit_count=1)
    body = client.get(f"/api/plans/{pid}/review", headers=auth).json()
    assert body["status"] == "final" and body["final"] is True
    assert body["score"] >= 90, body
    assert body["grade"] in ("S", "A")
    assert [d["key"] for d in body["dimensions"]] == ["checkin", "adherence", "streak", "adjust"]
    assert body["stats"]["tasks_done"] == body["stats"]["tasks_total"] == 18
    assert body["stats"]["longest_streak"] == 3
    assert body["highlights"], "执行好的方案要有亮点"
    assert body["suggestions"], "总要给一句下一步建议"


def test_review_of_half_done_plan_scores_lower(session_factory, client, auth):
    pid = seed_plan(session_factory, start=date.today() - timedelta(days=8),
                    title="半途而废方案", done_ratio=0.5, records=False)
    body = client.get(f"/api/plans/{pid}/review", headers=auth).json()
    assert body["score"] < 70, body
    assert body["stats"]["task_rate"] == 0.5
    checkin = next(d for d in body["dimensions"] if d["key"] == "checkin")
    assert "9/18" in checkin["detail"]
    assert any("打卡" in s for s in body["suggestions"])


def test_review_without_any_record_is_not_started(session_factory, client, auth):
    pid = seed_plan(session_factory, start=date.today() - timedelta(days=20),
                    title="完全没动方案", import_tasks=False, records=False)
    body = client.get(f"/api/plans/{pid}/review", headers=auth).json()
    assert body["status"] == "not_started"
    assert body["score"] is None
    assert "没有执行记录" in body["headline"]


def test_edits_outside_range_lose_adjust_points(session_factory, client, auth):
    good = seed_plan(session_factory, start=date.today() - timedelta(days=14),
                     title="改完还在区间", done_ratio=1.0, edit_count=2, edit_status="ok")
    bad = seed_plan(session_factory, start=date.today() - timedelta(days=25),
                    title="改完超区间", done_ratio=1.0, edit_count=2, edit_status="high")
    good_review = client.get(f"/api/plans/{good}/review", headers=auth).json()
    bad_review = client.get(f"/api/plans/{bad}/review", headers=auth).json()
    good_adj = next(d for d in good_review["dimensions"] if d["key"] == "adjust")
    bad_adj = next(d for d in bad_review["dimensions"] if d["key"] == "adjust")
    assert good_adj["score"] == 10 and bad_adj["score"] == 0
    assert good_review["score"] > bad_review["score"]


# ---------------- 停用即生成评价 ----------------

def test_archive_generates_review(client, auth, session_factory):
    pid = seed_plan(session_factory, start=date.today() - timedelta(days=2),
                    status="active", title="将被归档", done_ratio=1.0)
    resp = client.put(f"/api/plans/{pid}/archive", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "archived"
    assert body["review"]["score"] is not None
    assert body["review"]["final"] is True

    # 历史方案列表带评价摘要，卡片能直接显示分数
    card = {p["id"]: p for p in client.get("/api/plans", headers=auth).json()}[pid]
    assert card["review_score"] == body["review"]["score"]
    assert card["review_grade"] == body["review"]["grade"]


def test_replacing_active_plan_reviews_the_old_one(client, auth, session_factory):
    old_id = seed_plan(session_factory, start=date.today() - timedelta(days=1),
                       status="active", title="被顶掉的方案", done_ratio=1.0)
    new_id = seed_plan(session_factory, start=date.today(), status="archived",
                       title="新接管方案", done_ratio=0.0, records=False)
    resp = client.post(f"/api/plans/{new_id}/import", headers=auth, json={"mode": "week"})
    assert resp.status_code == 200, resp.text

    plans = {p["id"]: p for p in client.get("/api/plans", headers=auth).json()}
    assert plans[old_id]["status"] == "archived"
    assert plans[old_id]["review_score"] is not None, "被顶掉的方案也要留评价"
    assert plans[new_id]["status"] == "active"


def test_ongoing_plan_review_is_progress_only(session_factory, client, auth):
    pid = seed_plan(session_factory, start=date.today(), days=3, status="active",
                    title="还在执行中", done_ratio=0.5)
    body = client.get(f"/api/plans/{pid}/review", headers=auth).json()
    assert body["status"] == "ongoing" and body["final"] is False
    assert body["score"] is not None
    assert body["stats"]["tasks_done"] < body["stats"]["tasks_total"]


def test_shift_moves_pending_tasks_and_no_ghost_in_review(session_factory, client, auth):
    """方案顺延时，未打卡任务要跟着平移，不能变成旧日期上的幽灵任务（会稀释完成率）"""
    start = date.today() - timedelta(days=5)
    pid = seed_plan(session_factory, start=start, title="待顺延方案",
                    done_ratio=0.0, records=False)
    resp = client.post(f"/api/plans/{pid}/import", headers=auth, json={"mode": "week"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["shift"]["shift_days"] == 5

    review = client.get(f"/api/plans/{pid}/review", headers=auth).json()
    assert review["stats"]["tasks_total"] == 18, review["stats"]
    assert review["period"]["start"] == str(date.today())

    # 旧日期上没有残留的待办
    old_day = client.get(f"/api/check-ins?date={start}", headers=auth).json()["items"]
    assert not [t for t in old_day if t.get("plan_day_id")], "旧日期上不该留着这份方案的任务"


def test_reactivated_plan_clears_old_review(session_factory, client, auth):
    """重新启用 = 新一期：上一期评价留档，列表不再挂旧分数"""
    pid = seed_plan(session_factory, start=date.today() - timedelta(days=6),
                    title="待重新启用", done_ratio=0.0, records=False)
    resp = client.put(f"/api/plans/{pid}/archive", headers=auth)
    assert resp.json()["review"]["score"] is not None

    resp = client.post(f"/api/plans/{pid}/import", headers=auth, json={"mode": "week"})
    assert resp.status_code == 200, resp.text
    card = {p["id"]: p for p in client.get("/api/plans", headers=auth).json()}[pid]
    assert card["status"] == "active"
    assert card.get("review_score") is None, "重新启用后不该再显示上一期的分数"

    with session_factory() as db:
        from app.models.diet_plan import DietPlan
        plan = db.get(DietPlan, pid)
        assert (plan.basis_snapshot or {}).get("last_review"), "上一期评价要留档"
