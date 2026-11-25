"""AI 教练对话：把用户当天的吃/动/睡数据塞进 system prompt，再交给现成的 LLM 通道

设计要点：
- 不新建模型、不新增依赖，走 `get_llm_provider()`，云端没 key 时自动是 Mock
- 每轮都重新取当天数据，所以教练"开口就带数字"，不会说空话
- 只保留最近 8 条消息，避免上下文无限增长烧 token
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.check_in import CheckIn
from app.models.device import DeviceMetric
from app.models.food_record import FoodRecord
from app.models.health_profile import HealthProfile
from app.models.user import User
from app.services.llm.provider import get_llm_provider
from app.services.nutrition import ACTIVITY_FACTORS, calc_bmr

router = APIRouter(prefix="/coach", tags=["AI 教练"])

MAX_TURNS = 8


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class CoachChatIn(BaseModel):
    messages: list[ChatMessage]


def _context(db: Session, user: User) -> str:
    """把今天的数据拼成一段给模型看的事实清单"""
    today = date.today()
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    intake = float(db.query(func.coalesce(func.sum(FoodRecord.calories), 0.0))
                   .filter(FoodRecord.user_id == user.id, FoodRecord.eaten_date == today).scalar() or 0)
    rows = (db.query(DeviceMetric)
            .filter(DeviceMetric.user_id == user.id, DeviceMetric.metric_date == today).all())
    steps = sum(r.steps or 0 for r in rows)
    band_burn = sum(r.calories or 0 for r in rows)
    checkin_burn = float(db.query(func.coalesce(func.sum(CheckIn.calories_burned), 0.0))
                         .filter(CheckIn.user_id == user.id, CheckIn.check_type == "exercise",
                                 CheckIn.status == "done", CheckIn.check_date == today).scalar() or 0)

    bmr = calc_bmr(profile.weight_kg, profile.height_cm, profile.age, profile.gender) if profile else None
    factor = ACTIVITY_FACTORS.get((profile.activity_level if profile else "moderate") or "moderate", 1.55)
    tdee = round(bmr * factor) if bmr else 2000
    target = (profile.daily_calorie_target if profile and profile.daily_calorie_target else 2000)

    last_sleep = (db.query(DeviceMetric.sleep_minutes)
                  .filter(DeviceMetric.user_id == user.id,
                          DeviceMetric.metric_date <= today,
                          DeviceMetric.metric_date >= today - timedelta(days=13),
                          DeviceMetric.sleep_minutes.isnot(None))
                  .order_by(DeviceMetric.metric_date.desc()).first())
    sleep_min = last_sleep[0] if last_sleep else None
    sleep_txt = f"{sleep_min // 60} 小时 {sleep_min % 60} 分" if sleep_min else "未知"

    goal_txt = {"lose": "减脂", "gain": "增肌", "maintain": "维持体重"}.get(
        (profile.goal if profile else "") or "maintain", "维持体重")
    items = [
        f"目标：{goal_txt}，每日热量目标 {target} kcal",
        f"今日已摄入：{round(intake)} kcal",
        f"今日活动消耗：{round(band_burn + checkin_burn)} kcal（手环 {round(band_burn)} + 打卡 {round(checkin_burn)}）",
        f"基础代谢 {bmr or '未知'} kcal，按档案估算总消耗约 {tdee} kcal",
        f"今日步数：{steps}（目标 8000）",
        f"最近一晚睡眠：{sleep_txt}（目标 7.5 小时）",
    ]
    if profile and profile.weight_kg:
        items.append(f"体重 {profile.weight_kg} kg，身高 {profile.height_cm or '未知'} cm")
    return "\n".join(f"- {x}" for x in items)


@router.post("/chat")
def coach_chat(payload: CoachChatIn, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """AI 教练对话：返回模型回复 + 实际使用的模型名"""
    history = [m.model_dump() for m in payload.messages][-MAX_TURNS:]
    system = (
        "你是「识膳智行」里的私人营养与运动教练，负责根据用户的真实数据给建议。\n"
        "原则：\n"
        "1. 中文、口语化、每次不超过 4 句；先回答问题，再补一句可执行建议。\n"
        "2. 只使用下面给出的数据，缺数据就说没有，绝对不要编造数字。\n"
        "3. 涉及疾病、用药、极端节食等超出营养建议范围的问题，提示咨询医生。\n"
        f"\n用户当前数据：\n{_context(db, user)}"
    )
    provider = get_llm_provider()
    reply = provider.chat([{"role": "system", "content": system}, *history], max_tokens=500)
    return {
        "reply": (reply or "").strip(),
        "source": getattr(provider, "active_name", provider.name),
        "context_date": date.today().isoformat(),
    }
