"""聊天框改造：先答后问 + 后端注入档案（没有数据才会反问）"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  确保所有表注册
from app.db.session import Base, get_db
from app.main import app
from app.services import plan_targets
from app.services.llm.prompts import build_chat_system

PROFILE = {
    "height_cm": 172, "weight_kg": 78, "age": 26, "gender": "male",
    "activity_level": "moderate", "goal": "lose",
}


class FakeProvider:
    """只记录收到的 messages，不调真模型"""

    def __init__(self):
        self.messages = None

    def chat(self, messages):
        self.messages = messages
        return "你 172cm/78kg、中度活动，维持约 2450 kcal；1800 是 27% 赤字，偏激进。"


@pytest.fixture()
def client():
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def auth(client):
    resp = client.post("/api/auth/register", json={
        "username": "chat_ctx_user", "password": "chat123456",
    })
    assert resp.status_code == 200, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['token']}"}
    client.put("/api/profile", headers=headers, json=PROFILE)
    return headers


def test_chat_endpoint_injects_profile(client, auth, monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr("app.routers.plans.get_llm_provider", lambda: fake)
    resp = client.post("/api/plans/chat", headers=auth,
                       json={"messages": [{"role": "user", "content": "我 1800 大卡够吗"}]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["reply"]

    system = fake.messages[0]["content"]
    assert fake.messages[0]["role"] == "system"
    assert "172" in system and "78" in system          # 身高体重进提示词
    assert "减脂" in system                             # 诉求进提示词
    assert "kcal" in system                             # 当前目标区间进提示词


def test_system_asks_answer_first():
    system = build_chat_system(PROFILE, plan_targets.build_targets(PROFILE))
    assert "先直接回答" in system
    assert "不要开场就反问" in system


def test_system_without_profile_asks_for_basics():
    system = build_chat_system({}, {})
    assert "还没填" in system
    assert "一次问全" in system


def test_system_round_two_pushes_to_generate():
    system = build_chat_system(PROFILE, plan_targets.build_targets(PROFILE), round_no=2)
    assert "生成方案" in system


def test_chat_history_is_kept(client, auth, monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr("app.routers.plans.get_llm_provider", lambda: fake)
    client.post("/api/plans/chat", headers=auth, json={
        "messages": [
            {"role": "user", "content": "晚饭吃什么好"},
            {"role": "assistant", "content": "清淡一点。"},
            {"role": "user", "content": "那 1800 够吗"},
        ],
    })
    roles = [m["role"] for m in fake.messages[1:]]
    assert roles == ["user", "assistant", "user"]
