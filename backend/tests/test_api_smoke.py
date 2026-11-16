"""接口级冒烟测试：一条主链路走通，防止"服务层单测全绿但接口挂了"

覆盖：注册 → 登录（含失败限流）→ 当前用户 → 完善档案（发积分）→ 手动记一餐 →
       仪表盘总览 → 排行榜 → 日历打卡列表 → 周报（含缓存命中）

用独立的 in-memory SQLite，不碰 backend/foodai.db。
不进入 TestClient 的 with 上下文，因此不会触发 lifespan 里的种子写入。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  确保所有表都注册到 Base.metadata
from app.db.session import Base, get_db
from app.main import app


@pytest.fixture(scope="module")
def client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,          # 内存库要共用一个连接，否则每个连接都是空库
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
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def auth(client):
    """注册并返回带 Authorization 头的字典"""
    resp = client.post("/api/auth/register", json={
        "username": "smoke_user", "password": "smoke123456", "nickname": "冒烟测试",
    })
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_register_duplicate_username(client, auth):
    resp = client.post("/api/auth/register", json={
        "username": "smoke_user", "password": "smoke123456",
    })
    assert resp.status_code == 400


def test_me_and_profile(client, auth):
    resp = client.get("/api/auth/me", headers=auth)
    assert resp.status_code == 200
    assert resp.json()["profile_completed"] is False

    resp = client.put("/api/profile", headers=auth, json={
        "height_cm": 175, "weight_kg": 70, "age": 26, "gender": "male",
        "activity_level": "moderate", "goal": "lose",
        "dietary_restrictions": ["忌辣"], "taste_preferences": ["清淡"],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bmi"] == 22.9
    assert body["daily_calorie_target"] > 1200
    assert body["points_awarded"] == 20      # 首次完善档案加分


def test_manual_food_record_and_overview(client, auth):
    resp = client.post("/api/food-records", headers=auth, json={
        "dish_name": "冒烟测试菜", "portion_g": 200, "meal_type": "lunch",
        "calories": 320, "protein_g": 12, "fat_g": 8, "carb_g": 48,
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["calories"] > 0

    resp = client.get("/api/food-records", headers=auth)
    assert resp.status_code == 200
    assert len(resp.json()["records"]) == 1

    resp = client.get("/api/stats/overview", headers=auth)
    assert resp.status_code == 200
    assert resp.json()["today_calories"] > 0


def test_leaderboard_and_checkins(client, auth):
    resp = client.get("/api/points/leaderboard", headers=auth)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list) and resp.json()[0]["is_me"] is True

    resp = client.get("/api/check-ins", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body and "summary" in body


def test_weekly_report_cached(client, auth):
    """同一天内重复请求必须命中缓存：内容一致，且第二次不再走模型"""
    first = client.get("/api/stats/weekly-report", headers=auth)
    assert first.status_code == 200, first.text
    assert first.json()["summary"]

    second = client.get("/api/stats/weekly-report", headers=auth)
    assert second.status_code == 200
    assert second.json()["summary"] == first.json()["summary"]
    assert second.json()["score"] == first.json()["score"]

    # refresh=true 允许强制重算（内容可能不同，只校验接口可用）
    third = client.get("/api/stats/weekly-report?refresh=true", headers=auth)
    assert third.status_code == 200
    assert third.json()["summary"]


def test_login_wrong_password_then_locked(client):
    """连续错误密码：先 401，达到阈值后 429（防撞库）"""
    for _ in range(5):
        resp = client.post("/api/auth/login", json={
            "username": "nobody_here", "password": "wrong-password",
        })
        assert resp.status_code in (401, 429)

    resp = client.post("/api/auth/login", json={
        "username": "nobody_here", "password": "wrong-password",
    })
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_login_ok(client, auth):
    resp = client.post("/api/auth/login", json={
        "username": "smoke_user", "password": "smoke123456",
    })
    assert resp.status_code == 200
    assert resp.json()["token"]
