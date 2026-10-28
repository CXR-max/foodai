"""
FastAPI 应用入口
启动：cd backend && python -m uvicorn app.main:app --reload --port 8000
接口文档：http://localhost:8000/docs
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.reward import Reward
from app.routers import (
    achievements, auth, checkins, food_records, foods, plans, points, profile,
    recognitions, rewards, stats,
)
from app.services.achievement_seed import seed_achievements
from app.services.food_seed import seed_foods_if_empty

logger = logging.getLogger("app")
from app.services.llm.provider import get_llm_provider

# 预置的积分商城奖励（首次启动自动写入，想加奖励改这个列表）
# 注意：虚拟荣誉（头衔/徽章）已改为「成就解锁制」，不再用积分购买，故此处不包含 honor 类
SEED_REWARDS = [
    # 产品权益类
    ("补签卡", "privilege", "漏打卡的日子可以补签一次", 30, -1, "📅", "makeup"),
    ("方案定制券", "privilege", "生成方案时附加精细化定制要求", 80, -1, "⚡", "custom_plan"),
    ("专属头像框", "privilege", "解锁专属头像装饰框", 120, -1, "🎨", "avatar_frame"),
    # 实物/合作券类（虚拟库存 + 核销码，比赛演示用）
    ("健康零食礼包券", "goods", "低卡零食大礼包兑换券（线下核销）", 300, 20, "🥗", "physical"),
    ("运动周边盲盒券", "goods", "运动装备盲盒兑换券（线下核销）", 500, 10, "⚽", "physical"),
    ("健身房周体验卡", "goods", "合作健身房一周体验券（线下核销）", 800, 5, "💪", "physical"),
]


def seed_rewards():
    """首次启动时把奖励商品写进库（已有时跳过）"""
    db = SessionLocal()
    try:
        if db.query(Reward).count() == 0:
            for name, category, desc, cost, stock, icon, kind in SEED_REWARDS:
                db.add(Reward(name=name, category=category, description=desc,
                              points_cost=cost, stock=stock, icon=icon, kind=kind))
            db.commit()
            print(f"[种子] 已写入 {len(SEED_REWARDS)} 个商城奖励")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭钩子"""
    seed_rewards()
    seed_achievements(SessionLocal())
    seed_foods_if_empty()
    yield


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)


@app.middleware("http")
async def log_rejected_writes(request, call_next):
    """诊断用：识别校准类写请求被 4xx 拒绝时，把请求体打到日志。

    只记录 /api/recognitions 的 PATCH，避免把登录密码等敏感字段写进日志。
    """
    if request.method == "PATCH" and request.url.path.startswith("/api/recognitions"):
        body = await request.body()
        response = await call_next(request)
        if response.status_code >= 400:
            logger.warning("REJECT %s %s -> %s body=%s", request.method, request.url.path,
                           response.status_code, body[:600])
        return response
    return await call_next(request)

# CORS 兜底（开发时前端走 vite proxy，一般用不到；演示直连 8000 时需要）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 上传图片静态目录（/uploads/xxx.jpg）
app.mount("/uploads", StaticFiles(directory=str(settings.UPLOAD_DIR)), name="uploads")

# 业务路由，统一挂 /api 前缀
for r in (auth, profile, recognitions, food_records, foods, plans, checkins, stats, points, rewards, achievements):
    app.include_router(r.router, prefix="/api")


@app.get("/api/health")
def health():
    """健康检查"""
    return {"status": "ok", "app": settings.APP_NAME}


@app.get("/api/llm/status")
def llm_status(probe: bool = False):
    """查看当前模式与实际使用的模型。

    LLM_MODE=cloud → 文字+视觉都走云端智谱
    LLM_MODE=local → 文字走本地 Ollama，视觉仍走云端
    active_provider=mock 表示当前模式没配到可用 key（退回本地模拟）
    加 ?probe=1 会真的调用一次文字模型做连通性自检。
    """
    provider = get_llm_provider()
    info = {
        "LLM_MODE": settings.LLM_MODE,
        "is_local": settings.is_local,
        "active_provider": provider.name,            # mock / openai_compat
        "key_configured": bool(settings.text_api_key),
        "text_base_url": settings.text_base_url,
        "text_model": settings.text_model,
        "vision_base_url": settings.vision_base_url,
        "vision_model": settings.vision_model,
        "vision_key_configured": bool(settings.vision_api_key),
        "fallback_to_mock": settings.LLM_FALLBACK_TO_MOCK,
    }
    if probe:
        # 绕过 mock 兜底，直接测"真模型"本身：key 错/服务没起/网络不通会直接报错
        target = getattr(provider, "real", provider)
        try:
            reply = target.chat([{"role": "user", "content": "只回复两个字：正常"}])
            info["probe_ok"] = True
            info["probe_reply"] = (reply or "")[:50]
        except Exception as e:  # noqa: BLE001
            info["probe_ok"] = False
            info["probe_error"] = str(e)[:200]
    return info
