"""养生方案路由：AI 生成 / 查看 / 编辑某一天 / 归档
方案以 plan_days 的 JSON 存储，用户可改完再"导入日历"打卡
"""
from datetime import date, datetime, timedelta
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import SessionLocal, get_db
from app.models.check_in import CheckIn
from app.models.diet_plan import DietPlan, PlanDay
from app.models.health_profile import HealthProfile
from app.models.plan_chat import PlanChatMessage
from app.models.user import User
from app.schemas.plan import ChatMessageIn, GeneratePlanIn, PlanChatIn, PlanDayUpdateIn
from app.services import nutrition, points
from app.services.achievements import unlock_all
from app.services.llm import foods_data
from app.services.llm.prompts import PLAN_CHAT_SYSTEM
from app.services.llm.provider import get_llm_provider
from app.services.plan_intent import extract_plan_intent, merge_plan_intent

logger = logging.getLogger("plans")

router = APIRouter(prefix="/plans", tags=["养生方案"])

MEAL_ORDER = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}

MAX_CHAT_HISTORY = 20  # 聊天模型带最近 20 条；方案约束抽取另读持久化全量/最近 60 条


def build_chat_messages(history) -> list[dict]:
    """system + 最近若干条历史（裁剪旧消息，保证首字延迟稳定）"""
    recent = history[-MAX_CHAT_HISTORY:]
    return ([{"role": "system", "content": PLAN_CHAT_SYSTEM}]
            + [{"role": m.role, "content": m.content} for m in recent])


def _last_user_content(messages) -> str:
    for message in reversed(messages or []):
        if getattr(message, "role", "") == "user":
            return (getattr(message, "content", "") or "").strip()
    return ""


def save_chat_exchange(user_id: int, messages, assistant_text: str = "") -> None:
    """把一轮对话写入数据库。

    新用户/首次部署时 messages 里可能带着整段历史，这里一并落库；
    已有历史时只补当前新消息，避免每轮重复插入。
    """
    incoming = [
        (getattr(m, "role", ""), (getattr(m, "content", "") or "").strip())
        for m in (messages or [])
        if getattr(m, "role", "") in ("user", "assistant")
        and (getattr(m, "content", "") or "").strip()
    ]
    if not incoming and not (assistant_text or "").strip():
        return

    db = SessionLocal()
    try:
        existing_rows = (
            db.query(PlanChatMessage)
            .filter(PlanChatMessage.user_id == user_id)
            .order_by(PlanChatMessage.id.desc())
            .limit(100)
            .all()
        )
        existing = [(r.role, r.content) for r in reversed(existing_rows)]

        if not existing:
            to_add = incoming
        elif len(existing) <= len(incoming) and incoming[:len(existing)] == existing:
            to_add = incoming[len(existing):]
        elif len(existing) <= len(incoming) and incoming[-len(existing):] == existing:
            to_add = incoming[:-len(existing)]
        else:
            to_add = incoming[-1:]  # 至少保存当前这条

        # 流式/同步重试时，最后一条 user 已存在且很近，不重复写
        if to_add and to_add[-1][0] == "user" and existing_rows:
            last = existing_rows[0]
            if (
                last.role == "user"
                and last.content == to_add[-1][1]
                and last.created_at
                and (datetime.now() - last.created_at).total_seconds() < 8
            ):
                to_add = to_add[:-1]

        for role, content in to_add:
            db.add(PlanChatMessage(user_id=user_id, role=role, content=content))

        assistant_text = (assistant_text or "").strip()
        if assistant_text:
            db.add(PlanChatMessage(user_id=user_id, role="assistant", content=assistant_text))

        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("保存对话历史失败")
    finally:
        db.close()


def load_chat_history(user_id: int, limit: int = 60) -> list[dict]:
    """按时间正序返回最近 limit 条对话（给前端恢复历史 / 给约束抽取用）。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(PlanChatMessage)
            .filter(PlanChatMessage.user_id == user_id)
            .order_by(PlanChatMessage.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in reversed(rows)
        ]
    finally:
        db.close()


def history_text_for_intent(user_id: int, fallback: str = "") -> str:
    """把持久化历史拼成带时间戳的文本；首次部署没有 DB 记录时导入前端历史。"""
    if not load_chat_history(user_id, 1) and fallback.strip():
        import_chat_text(user_id, fallback)

    rows = load_chat_history(user_id, 60)
    lines: list[str] = []
    for row in rows:
        content = (row.get("content") or "").replace("\n", " ")[:600]
        created = row.get("created_at") or ""
        # ISO: 2026-10-20T14:00:00 -> 10-20 14:00
        stamp = created[5:16].replace("T", " ") if len(created) >= 16 else ""
        who = "用户" if row.get("role") == "user" else "AI"
        lines.append(f"[{stamp}] {who}：{content}" if stamp else f"{who}：{content}")

    # 从最新往回保留，防止把本地 7B 的 4K 上下文撑爆
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        if kept and total + len(line) > 2600:
            break
        kept.append(line)
        total += len(line) + 1
    return "\n".join(reversed(kept))


def import_chat_text(user_id: int, text: str) -> None:
    """把前端传上来的 '用户：... / AI：...' 文本导入数据库（只在 DB 为空时用）。"""
    messages: list[ChatMessageIn] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("用户：") or stripped.startswith("用户:"):
            messages.append(ChatMessageIn(role="user", content=stripped[3:].strip()))
        elif stripped.startswith("AI：") or stripped.startswith("AI:"):
            messages.append(ChatMessageIn(role="assistant", content=stripped[3:].strip()))
    if messages:
        save_chat_exchange(user_id, messages, "")


def get_history_text(user_id: int, fallback: str = "") -> str:
    return history_text_for_intent(user_id, fallback)


def sync_plan_day_checkins(db: Session, user_id: int, day: PlanDay) -> None:
    """方案天被修改后，同步日历里由它导入的待办任务。

    说明：日历展示的餐食内容是通过 plan_day_id 实时读取方案天的（见 checkins.checkin_dict），
    所以"改菜名/克重"会自动同步；这里只处理"结构变化"——某餐被删/新增、运动有无变化。
    为避免抹掉历史，只增删/移动 pending 任务，已完成的 done 任务保留不动。
    """
    existing = {c.task_key: c for c in db.query(CheckIn).filter(
        CheckIn.user_id == user_id, CheckIn.plan_day_id == day.id).all()}
    if not existing:
        return   # 该天从未"导入日历"：不自动创建任务，保持由用户主动导入

    desired: dict[str, str] = {}
    for meal in day.meals or []:
        desired[f"meal_{meal.get('meal_type', 'snack')}"] = "diet"
    if day.exercise:
        desired["exercise"] = "exercise"

    for key, ctype in desired.items():
        c = existing.get(key)
        if c is None:
            db.add(CheckIn(user_id=user_id, check_date=day.date, check_type=ctype,
                           task_key=key, status="pending", plan_day_id=day.id))
        elif c.status == "pending":
            c.check_date = day.date

    for key, c in existing.items():
        if key not in desired and c.status == "pending":
            db.delete(c)


def recalc_meal_calories(meal: dict) -> float:
    """按营养库重算一餐热量；若所有菜品都没收录，则保留模型给的值"""
    total, any_matched = 0.0, False
    for item in meal.get("items") or []:
        entry = foods_data.find_food(item.get("name", ""))
        if entry:
            any_matched = True
            total += entry["per_100g"]["calories"] * (item.get("portion_g") or 100) / 100
    if any_matched:
        return round(total)
    return round(meal.get("calories") or 0)


def normalize_meals(meals: list[dict]) -> list[dict]:
    """餐次排序统一为 早→午→晚→加餐"""
    return sorted(meals, key=lambda m: MEAL_ORDER.get(m.get("meal_type", "snack"), 9))


def plan_day_dict(d: PlanDay) -> dict:
    return {
        "id": d.id, "day_index": d.day_index, "date": str(d.date),
        "theme": d.theme, "meals": d.meals or [],
        "exercise": d.exercise,
        "estimated_calories": d.estimated_calories,
    }


def plan_dict(p: DietPlan, with_days: bool = False, db: Session | None = None) -> dict:
    result = {
        "id": p.id, "title": p.title, "status": p.status,
        "start_date": str(p.start_date), "summary": p.summary,
        "daily_target_calories": p.daily_target_calories,
        "provider": p.provider,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
    if with_days and db is not None:
        days = (db.query(PlanDay).filter(PlanDay.plan_id == p.id)
                .order_by(PlanDay.day_index.asc()).all())
        result["days"] = [plan_day_dict(d) for d in days]
    return result


def build_profile_ctx(profile: HealthProfile, data: GeneratePlanIn) -> dict:
    """组装档案上下文（含前端偏好面板的覆盖）"""
    ctx = {
        "height_cm": profile.height_cm, "weight_kg": profile.weight_kg,
        "bmi": nutrition.calc_bmi(profile.weight_kg, profile.height_cm),
        "age": profile.age, "gender": profile.gender,
        "activity_level": profile.activity_level or "moderate",
        "goal": profile.goal or "maintain",
        "daily_target_calories": profile.daily_calorie_target
        or nutrition.calc_daily_target(profile.weight_kg, profile.height_cm,
                                       profile.age, profile.gender,
                                       profile.activity_level, profile.goal),
        "taste_preferences": profile.taste_preferences or [],
        "dietary_restrictions": profile.dietary_restrictions or [],
        "exercise_preferences": profile.exercise_preferences or [],
        # 面板是否显式选择（用于对话"最新表态"覆盖档案旧口味）
        "panel_taste_explicit": data.taste_preferences is not None,
        "panel_restrictions_explicit": data.dietary_restrictions is not None,
    }
    if data.taste_preferences is not None:
        ctx["taste_preferences"] = data.taste_preferences
    if data.dietary_restrictions is not None:
        ctx["dietary_restrictions"] = data.dietary_restrictions
    if data.exercise_preferences is not None:
        ctx["exercise_preferences"] = data.exercise_preferences
    return ctx


def combine_note(data: GeneratePlanIn) -> str:
    """把备注与聊天文本合并成补充要求"""
    note = data.note or ""
    if data.chat_text.strip():
        note = (note + "\n" if note else "") + f"用户在对话中补充：{data.chat_text.strip()}"
    return note


@router.post("/generate")
def generate_plan(
    data: GeneratePlanIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """AI 生成方案（同步等待 LLM，前端此请求单独设置 120s 超时）"""
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    if not (profile and profile.height_cm and profile.weight_kg and profile.age):
        raise HTTPException(status_code=400, detail="请先完善健康档案（身高/体重/年龄），AI 才能为你定制方案")

    # 组装档案上下文 + 合并偏好面板与聊天文本
    profile_ctx = build_profile_ctx(profile, data)
    combined_note = combine_note(data)

    provider = get_llm_provider()
    # 先把持久化对话翻译成"当前有效"的结构化约束，再交给求解器
    history_text = get_history_text(user.id, data.chat_text)
    intent = extract_plan_intent(provider, profile_ctx, data.note, history_text)
    profile_ctx = merge_plan_intent(profile_ctx, intent)
    plan_result = provider.generate_plan(profile_ctx, data.days, combined_note)

    # 落库：主表 + 每日明细（餐次热量按营养库重算）
    start = date.today()
    plan = DietPlan(
        user_id=user.id,
        title=f"{data.days}天方案 {datetime.now().strftime('%m-%d %H:%M')}",
        start_date=start,
        summary=plan_result.summary,
        daily_target_calories=int(plan_result.daily_target_calories or profile_ctx["daily_target_calories"]),
        provider=provider.active_name,   # 兜底到 mock 时如实标注
        basis_snapshot=profile_ctx,   # 留档：可追溯"当时是根据什么生成的"
    )
    db.add(plan)
    db.flush()  # 拿到 plan.id
    for day in plan_result.days:
        meals = normalize_meals([m.model_dump() for m in day.meals])
        for m in meals:
            m["calories"] = recalc_meal_calories(m)
        db.add(PlanDay(
            plan_id=plan.id,
            day_index=day.day_index,
            date=start + timedelta(days=day.day_index - 1),
            meals=meals,
            exercise=day.exercise.model_dump() if day.exercise else None,
            estimated_calories=round(sum(m["calories"] for m in meals)),
        ))
    pts = points.award(db, user, "generate_plan", description=f"生成{data.days}天养生方案")
    unlock_all(db, user)   # 生成方案数成就
    db.commit()
    db.refresh(plan)
    result = plan_dict(plan, with_days=True, db=db)
    result["points_awarded"] = pts or 0
    return result


@router.post("/generate-stream")
def generate_plan_stream(
    data: GeneratePlanIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """逐天流式生成方案（SSE）：每天生成完立即推送，全部完成后落库并返回 plan_id"""
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    if not (profile and profile.height_cm and profile.weight_kg and profile.age):
        raise HTTPException(status_code=400, detail="请先完善健康档案（身高/体重/年龄），AI 才能为你定制方案")

    profile_ctx = build_profile_ctx(profile, data)
    combined_note = combine_note(data)
    provider = get_llm_provider()
    # 对话 → 当前有效约束（最新表态覆盖旧表态）→ 与勾选偏好合并
    history_text = get_history_text(user.id, data.chat_text)
    intent = extract_plan_intent(provider, profile_ctx, data.note, history_text)
    profile_ctx = merge_plan_intent(profile_ctx, intent)
    user_id = user.id

    def sse(event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def stream():
        start = date.today()
        collected: list[dict] = []
        try:
            for i in range(1, data.days + 1):
                # 开始第 i 天：先推 0 进度，前端立刻显示"第 i 天生成中"
                yield sse("progress", {"day_index": i, "chars": 0})
                day = None
                chars = last_sent = 0
                for event in provider.generate_day_stream(
                        profile_ctx, i, data.days, collected, combined_note):
                    if event["type"] == "delta":
                        chars += len(event["text"])
                        if chars - last_sent >= 60:      # 每 60 字推一次，避免刷屏
                            last_sent = chars
                            yield sse("progress", {"day_index": i, "chars": chars})
                    elif event["type"] == "day":
                        day = event["day"]
                if day is None:
                    raise RuntimeError("模型未返回当天方案")
                meals = normalize_meals([m.model_dump() for m in day.meals])
                for m in meals:
                    m["calories"] = recalc_meal_calories(m)
                d = {
                    "id": -1, "day_index": i, "date": str(start + timedelta(days=i - 1)),
                    "theme": day.theme, "meals": meals,
                    "exercise": day.exercise.model_dump() if day.exercise else None,
                    "estimated_calories": round(sum(m["calories"] for m in meals)),
                }
                collected.append(d)
                yield sse("day", d)
        except Exception as e:  # noqa: BLE001
            yield sse("error", {"message": f"生成失败：{str(e)[:150]}"})
            return

        # 全部生成完 → 落库（新开会话，避免流式期间请求 session 生命周期问题）
        summary = f"已按你的偏好与目标生成 {data.days} 天渐进式方案"
        intent_summary = str(profile_ctx.get("intent_summary") or "").strip()
        if intent_summary:
            summary += f"；已结合对话：{intent_summary}"
        plan_note = getattr(provider, "plan_note", "")
        if plan_note:
            summary += f"；{plan_note}"
        variety_note = getattr(provider, "variety_note", "")
        if variety_note:
            summary += f"；{variety_note}"
        s = SessionLocal()
        try:
            plan = DietPlan(
                user_id=user_id,
                title=f"{data.days}天方案 {datetime.now().strftime('%m-%d %H:%M')}",
                start_date=start,
                summary=summary,
                daily_target_calories=int(profile_ctx.get("daily_target_calories") or 2000),
                provider=provider.active_name,   # 兜底到 mock 时如实标注
                basis_snapshot=profile_ctx,
            )
            s.add(plan)
            s.flush()
            for d in collected:
                s.add(PlanDay(
                    plan_id=plan.id, day_index=d["day_index"],
                    date=start + timedelta(days=d["day_index"] - 1),
                    meals=d["meals"], exercise=d["exercise"],
                    estimated_calories=d["estimated_calories"],
                ))
            u = s.get(User, user_id)
            pts = points.award(s, u, "generate_plan", description=f"生成{data.days}天养生方案") if u else 0
            if u:
                unlock_all(s, u)   # 生成方案数成就
            s.commit()
            yield sse("done", {
                "plan_id": plan.id, "title": plan.title, "summary": plan.summary,
                "daily_target_calories": plan.daily_target_calories, "points_awarded": pts or 0,
            })
        except Exception as e:  # noqa: BLE001
            s.rollback()
            yield sse("error", {"message": f"保存失败：{str(e)[:150]}"})
        finally:
            s.close()

    return StreamingResponse(
        stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("")
def list_plans(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """我的方案列表（不含每日明细）"""
    plans = (db.query(DietPlan).filter(DietPlan.user_id == user.id)
             .order_by(DietPlan.created_at.desc()).all())
    return [plan_dict(p) for p in plans]


@router.get("/chat/history")
def get_chat_history(
    limit: int = 60,
    user: User = Depends(get_current_user),
):
    """恢复持久化 AI 对话历史（按时间正序）。"""
    return load_chat_history(user.id, min(max(limit, 1), 200))


@router.delete("/chat/history")
def delete_chat_history(user: User = Depends(get_current_user)):
    """清空当前用户的 AI 对话历史。"""
    db = SessionLocal()
    try:
        deleted = (
            db.query(PlanChatMessage)
            .filter(PlanChatMessage.user_id == user.id)
            .delete(synchronize_session=False)
        )
        db.commit()
        return {"deleted": deleted}
    finally:
        db.close()


@router.get("/{plan_id}")
def get_plan(plan_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """方案详情（含 7 天明细）"""
    plan = db.get(DietPlan, plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="方案不存在")
    return plan_dict(plan, with_days=True, db=db)


@router.put("/{plan_id}/days/{day_index}")
def update_plan_day(
    plan_id: int,
    day_index: int,
    data: PlanDayUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """编辑方案某一天（对 AI 方案不满意可修改，改完再导入日历）"""
    plan = db.get(DietPlan, plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="方案不存在")
    day = (db.query(PlanDay)
           .filter(PlanDay.plan_id == plan.id, PlanDay.day_index == day_index).first())
    if not day:
        raise HTTPException(status_code=404, detail="方案天不存在")

    if data.theme is not None:
        day.theme = data.theme
    if data.meals is not None:
        meals = normalize_meals([m.model_dump() for m in data.meals])
        for m in meals:   # 编辑后热量按营养库重算
            m["calories"] = recalc_meal_calories(m)
        day.meals = meals
    if data.exercise is not None:
        day.exercise = data.exercise.model_dump()

    day.estimated_calories = round(sum((m.get("calories") or 0) for m in (day.meals or [])))
    sync_plan_day_checkins(db, user.id, day)   # 已导入日历的该天任务同步
    db.commit()
    return plan_day_dict(day)


@router.post("/chat")
def chat_with_llm(
    data: PlanChatIn,
    user: User = Depends(get_current_user),
):
    """生成方案前的纯文字沟通：前端携带历史消息，后端返回模型回复"""
    provider = get_llm_provider()
    messages = build_chat_messages(data.messages)
    reply = provider.chat(messages)
    save_chat_exchange(user.id, data.messages, reply)
    return {"reply": reply}


@router.post("/chat-stream")
def chat_stream(
    data: PlanChatIn,
    user: User = Depends(get_current_user),
):
    """流式对话（SSE）：逐块推送模型回复，前端边收边显示（体感更快）"""
    provider = get_llm_provider()
    messages = build_chat_messages(data.messages)
    user_id = user.id

    def sse(event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def stream():
        parts: list[str] = []
        try:
            for piece in provider.chat_stream(messages):
                if piece:
                    parts.append(piece)
                    yield sse("delta", {"t": piece})
            save_chat_exchange(user_id, data.messages, "".join(parts))
            yield sse("done", {})
        except Exception as e:  # noqa: BLE001
            save_chat_exchange(user_id, data.messages, "".join(parts))
            yield sse("error", {"message": f"对话失败：{str(e)[:150]}"})

    return StreamingResponse(
        stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.put("/{plan_id}/archive")
def archive_plan(plan_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """归档旧方案"""
    plan = db.get(DietPlan, plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="方案不存在")
    plan.status = "archived"
    db.commit()
    return {"ok": True, "status": plan.status}
