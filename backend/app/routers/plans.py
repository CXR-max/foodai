"""养生方案路由：AI 生成 / 查看 / 编辑某一天 / 归档
方案以 plan_days 的 JSON 存储，用户可改完再"导入日历"打卡
"""
from datetime import date, datetime, timedelta
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import SessionLocal, get_db
from app.models.check_in import CheckIn
from app.models.diet_plan import DietPlan, PlanDay
from app.models.health_profile import HealthProfile
from app.models.plan_chat import PlanChatMessage
from app.models.user import User
from app.schemas.plan import (
    ChatMessageIn, GeneratePlanIn, PlanChatIn, PlanDayPreviewIn, PlanDayUpdateIn,
    PlanImportIn, ReusePlanIn,
)
from app.services import (
    meal_recalc, nutrition, plan_lifecycle, plan_review, plan_targets, points, schedule_import,
)
from app.services.achievements import unlock_all
from app.services.day_context import active_plan_day
from app.services.llm.prompts import PLAN_CHAT_SYSTEM
from app.services.llm.provider import get_llm_provider
from app.services.plan_intent import extract_plan_intent, merge_plan_intent
from app.services.planner import THEMES as PLAN_THEMES

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
        return   # 该天从未"导入日程"：不自动创建任务，保持由用户主动导入

    desired = schedule_import.desired_tasks(day)

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


def normalize_meals(meals: list[dict]) -> list[dict]:
    """餐次排序统一为 早→午→晚→加餐"""
    return sorted(meals, key=lambda m: MEAL_ORDER.get(m.get("meal_type", "snack"), 9))


def plan_day_dict(d: PlanDay) -> dict:
    exercises = d.exercises or ([d.exercise] if d.exercise else [])
    return {
        "id": d.id, "day_index": d.day_index, "date": str(d.date),
        "theme": d.theme or PLAN_THEMES[(d.day_index - 1) % len(PLAN_THEMES)],
        "meals": d.meals or [],
        "exercise": d.exercise,
        "exercises": exercises,
        "sleep": d.sleep,
        "targets": d.targets,
        "water_ml": d.water_ml,
        "estimated_calories": d.estimated_calories,
    }


def plan_dict(
    p: DietPlan,
    with_days: bool = False,
    db: Session | None = None,
    stats: dict | None = None,
) -> dict:
    snapshot = p.basis_snapshot or {}
    result = {
        "id": p.id, "title": p.title, "status": p.status,
        "start_date": str(p.start_date), "summary": p.summary,
        "daily_target_calories": p.daily_target_calories,
        "targets": p.targets,
        "reused_from": p.reused_from,
        "plan_version": p.plan_version or 1,
        "provider": p.provider,
        # 从历史方案重新启用过的痕迹（列表/详情用来提示「已重新启用」）
        "reactivated_at": snapshot.get("reactivated_at"),
        "reactivate_count": snapshot.get("reactivate_count") or 0,
        "original_start_date": snapshot.get("original_start_date"),
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
    # 停用评价摘要：列表卡片直接显示分数，详情再给完整版
    review = p.review or {}
    if review:
        result["review_score"] = review.get("score")
        result["review_grade"] = review.get("grade")
        result["review_headline"] = review.get("headline")
        result["review_status"] = review.get("status")
        result["review_task_rate"] = (review.get("stats") or {}).get("task_rate")
    if stats:
        result.update(stats)
    if with_days and db is not None:
        days = (db.query(PlanDay).filter(PlanDay.plan_id == p.id)
                .order_by(PlanDay.day_index.asc()).all())
        result["days"] = [plan_day_dict(d) for d in days]
        result["review"] = p.review   # 详情才带完整评价
    return result


def plan_stats(days: list[PlanDay]) -> dict:
    """列表卡片用的汇总：天数 / 日均热量 / 运动总消耗 / 结束日期"""
    if not days:
        return {"days_count": 0}
    total_cal = sum((d.estimated_calories or 0) for d in days)
    total_burn = 0
    for d in days:
        for item in (d.exercises or ([d.exercise] if d.exercise else [])):
            total_burn += int(item.get("calories_burned") or 0)
    return {
        "days_count": len(days),
        "end_date": max(str(d.date) for d in days),
        "avg_calories": round(total_cal / len(days)),
        "total_burn": total_burn,
        "latest_day_index": max(d.day_index for d in days),
    }


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
        "sleep_time": profile.sleep_time,
        "wake_time": profile.wake_time,
        "water_target_ml": profile.water_target_ml,
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


def normalize_generated_day(
    profile_ctx: dict,
    targets: dict,
    day_index: int,
    day,
    prior_days: list | None = None,
) -> dict:
    """统一整理模型/求解器产出的一天。

    模型只负责「吃什么」；运动处方、睡眠方案、当日目标一律由本地目标体系补，
    这样云端模型与本地求解器产出的结构完全一致，前端不用写两套逻辑。

    热量校准：模型给出的份量经常明显偏离目标（实测 1 天只有 760 kcal，目标 2000）。
    偏离超过 15% 时用本地求解器重排这一天的餐食，保证「计划里写的上限」和实际餐单对得上；
    求解失败则保留模型结果，不阻断生成。
    """
    meals = normalize_meals([m.model_dump() for m in day.meals])
    meals = meal_recalc.recalc_meals(meals)["meals"]
    total = sum((m.get("calories") or 0) for m in meals)

    cal_min = float(targets.get("calorie_min") or 0)
    cal_max = float(targets.get("calorie_max") or 0)
    calibrated = False
    if meals and cal_min and (total < cal_min * 0.85 or total > cal_max * 1.05):
        try:
            from app.services import planner

            fallback = planner.build_day(profile_ctx, day_index, 7, prior_days or [])
            meals = normalize_meals([m.model_dump() for m in fallback.meals])
            meals = meal_recalc.recalc_meals(meals)["meals"]
            calibrated = True
            logger.info("第 %d 天模型餐食 %.0f kcal 偏离目标，已用求解器重排", day_index, total)
        except Exception as exc:  # noqa: BLE001 —— 校准失败不影响生成
            logger.warning("第 %d 天热量校准失败，保留模型餐食：%s", day_index, exc)

    extra = plan_targets.enrich_day(profile_ctx, targets, day_index)
    day_targets = dict(extra["targets"])
    if calibrated:
        day_targets["calorie_source"] = "solver_calibrated"
    return {
        "day_index": day_index,
        "theme": day.theme or PLAN_THEMES[(day_index - 1) % len(PLAN_THEMES)],
        "meals": meals,
        "exercise": extra["exercise"],
        "exercises": extra["exercises"],
        "sleep": extra["sleep"],
        "targets": day_targets,
        "water_ml": extra["water_ml"],
        "estimated_calories": round(sum((m.get("calories") or 0) for m in meals)),
    }


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
    targets = plan_targets.build_targets(profile_ctx)
    plan = DietPlan(
        user_id=user.id,
        title=f"{data.days}天方案 {datetime.now().strftime('%m-%d %H:%M')}",
        start_date=start,
        status="active",
        summary=plan_result.summary,
        daily_target_calories=int(plan_result.daily_target_calories or profile_ctx["daily_target_calories"]),
        targets=targets,
        plan_version=1,
        provider=provider.active_name,   # 兜底到 mock 时如实标注
        basis_snapshot=profile_ctx,   # 留档：可追溯"当时是根据什么生成的"
    )
    db.add(plan)
    db.flush()  # 拿到 plan.id
    for day in plan_result.days:
        d = normalize_generated_day(
            profile_ctx, targets, day.day_index, day,
            prior_days=plan_result.days[:day.day_index - 1],
        )
        db.add(PlanDay(
            plan_id=plan.id,
            day_index=d["day_index"],
            date=start + timedelta(days=d["day_index"] - 1),
            meals=d["meals"],
            exercise=d["exercise"],
            exercises=d["exercises"],
            sleep=d["sleep"],
            targets=d["targets"],
            water_ml=d["water_ml"],
            estimated_calories=d["estimated_calories"],
        ))
    pts = points.award(db, user, "generate_plan", description=f"生成{data.days}天养生方案")
    unlock_all(db, user)   # 生成方案数成就
    plan_lifecycle.activate_plan(db, user.id, plan)   # 新方案即进行中，旧方案归档
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
    targets = plan_targets.build_targets(profile_ctx)
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
                d = normalize_generated_day(profile_ctx, targets, i, day, prior_days=collected)
                d["id"] = -1
                d["date"] = str(start + timedelta(days=i - 1))
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
                status="active",
                summary=summary,
                daily_target_calories=int(profile_ctx.get("daily_target_calories") or 2000),
                targets=targets,
                plan_version=1,
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
                    exercises=d["exercises"], sleep=d["sleep"],
                    targets=d["targets"], water_ml=d["water_ml"],
                    estimated_calories=d["estimated_calories"],
                ))
            u = s.get(User, user_id)
            pts = points.award(s, u, "generate_plan", description=f"生成{data.days}天养生方案") if u else 0
            if u:
                unlock_all(s, u)   # 生成方案数成就
            plan_lifecycle.activate_plan(s, user_id, plan)   # 新方案即进行中，旧方案归档
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
def list_plans(
    status: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """我的方案列表（不含每日明细，但带天数/日均热量/运动总消耗，手机上可当卡片用）"""
    query = db.query(DietPlan).filter(DietPlan.user_id == user.id)
    if status:
        query = query.filter(DietPlan.status == status)
    plans = query.order_by(DietPlan.created_at.desc()).all()
    if not plans:
        return []
    # 进行中的排最前，历史方案（已归档）按时间倒序跟在后面
    plans = sorted(plans, key=lambda p: (
        0 if p.status == "active" else 1,
        -(p.created_at.timestamp() if p.created_at else 0),
    ))

    ids = [p.id for p in plans]
    rows = (db.query(PlanDay).filter(PlanDay.plan_id.in_(ids))
            .order_by(PlanDay.day_index.asc()).all())
    grouped: dict[int, list[PlanDay]] = {}
    for row in rows:
        grouped.setdefault(row.plan_id, []).append(row)
    return [plan_dict(p, stats=plan_stats(grouped.get(p.id, []))) for p in plans]


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


@router.get("/active")
def get_active_plan(
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """当天生效的方案天 + 当日目标（日程 / 运动 / 我的 都读这个）"""
    day = date.fromisoformat(date_str) if date_str else date.today()
    plan_day = active_plan_day(db, user.id, day)
    if not plan_day:
        return {"has_plan": False, "date": str(day), "targets": day_context_targets(db, user.id, day)}
    plan = db.get(DietPlan, plan_day.plan_id)
    return {
        "has_plan": True,
        "date": str(day),
        "plan": plan_dict(plan),
        "day": plan_day_dict(plan_day),
        "targets": day_context_targets(db, user.id, day),
    }


def day_context_targets(db: Session, user_id: int, day: date) -> dict:
    from app.services.day_context import targets_for_day
    return targets_for_day(db, user_id, day)


@router.post("/{plan_id}/reuse")
def reuse_plan(
    plan_id: int,
    data: ReusePlanIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """沿用历史方案排下一周：日期顺延，任务不自动生成，需再导入日程。

    mode=copy    原样复制（连菜名一起）
    mode=reframe 保留框架（目标/运动/睡眠/饮水）重算餐食，第二周不会吃的一模一样
    """
    source = db.get(DietPlan, plan_id)
    if not source or source.user_id != user.id:
        raise HTTPException(status_code=404, detail="方案不存在")
    days = (db.query(PlanDay).filter(PlanDay.plan_id == source.id)
            .order_by(PlanDay.day_index.asc()).all())
    if not days:
        raise HTTPException(status_code=400, detail="该方案没有每日明细，无法沿用")

    today = date.today()
    last_end = max((d.date for d in days if d.date), default=None)
    start = data.start_date or ((last_end + timedelta(days=1)) if last_end else today)
    if start < today:      # 沿用的原方案早已结束，排期不能落回过去
        start = today
    profile_ctx = dict(source.basis_snapshot or {})
    targets = dict(source.targets or plan_targets.build_targets(profile_ctx))

    rebuilt: list[dict] = []
    prior: list = []
    reframed = 0
    for day in days:
        payload = {
            "day_index": day.day_index,
            "theme": day.theme,
            "meals": day.meals or [],
            "exercise": day.exercise,
            "exercises": day.exercises or ([day.exercise] if day.exercise else []),
            "sleep": day.sleep,
            "targets": day.targets,
            "water_ml": day.water_ml,
            "estimated_calories": day.estimated_calories,
        }
        if data.mode == "reframe":
            try:
                from app.services import planner

                generated = planner.build_day(profile_ctx, day.day_index, len(days), prior)
                payload = normalize_generated_day(
                    profile_ctx, targets, day.day_index, generated, prior_days=prior,
                )
                prior.append(generated)
                reframed += 1
            except Exception as exc:  # noqa: BLE001 —— 求解失败就退回原样复制该天
                logger.warning("沿用方案第 %d 天重算失败，退回复制：%s", day.day_index, exc)
        rebuilt.append(payload)

    plan = DietPlan(
        user_id=user.id,
        title=f"{len(days)}天方案（沿用）{start.strftime('%m-%d')}",
        start_date=start,
        status="active",
        summary=f"沿用《{source.title or '历史方案'}》的框架"
                + ("，并按当前营养库重算了餐食" if reframed else "，原样复制"),
        daily_target_calories=source.daily_target_calories,
        targets=targets,
        reused_from=source.id,
        plan_version=(source.plan_version or 1) + 1,
        provider="reuse",
        basis_snapshot=profile_ctx,
    )
    db.add(plan)
    db.flush()
    for payload in rebuilt:
        db.add(PlanDay(
            plan_id=plan.id,
            day_index=payload["day_index"],
            date=start + timedelta(days=payload["day_index"] - 1),
            meals=payload["meals"],
            exercise=payload["exercise"],
            exercises=payload["exercises"],
            sleep=payload["sleep"],
            targets=payload["targets"],
            water_ml=payload["water_ml"],
            estimated_calories=payload["estimated_calories"],
        ))
    activated = plan_lifecycle.activate_plan(db, user.id, plan)
    db.commit()
    db.refresh(plan)
    result = plan_dict(plan, with_days=True, db=db)
    result["activated"] = activated
    return result


@router.post("/{plan_id}/import")
def import_plan_to_schedule(
    plan_id: int,
    data: PlanImportIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """导入日程：mode=day 导入某一天，mode=week 一次导入整周。

    导入 = 这份方案开始生效：方案置为「进行中」，其它进行中的归档到历史方案。
    历史方案日期早已过去时，先整份顺延到今天，否则任务会落在过去、当天打不了卡。
    """
    plan = db.get(DietPlan, plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="方案不存在")

    days = (db.query(PlanDay).filter(PlanDay.plan_id == plan.id)
            .order_by(PlanDay.day_index.asc()).all())
    if not days:
        raise HTTPException(status_code=400, detail="该方案没有每日明细")

    shifted = plan_lifecycle.shift_to_anchor(db, plan, data.anchor or date.today())
    db.flush()
    if shifted["shift_days"]:
        days = (db.query(PlanDay).filter(PlanDay.plan_id == plan.id)
                .order_by(PlanDay.day_index.asc()).all())

    if data.mode == "day":
        if data.day_index is None:
            raise HTTPException(status_code=400, detail="导入单天需要指定 day_index")
        days = [d for d in days if d.day_index == data.day_index]
        if not days:
            raise HTTPException(status_code=404, detail="方案天不存在")

    result = schedule_import.import_plan_days(db, user.id, days)
    activated = plan_lifecycle.activate_plan(db, user.id, plan)
    db.commit()

    day_count = len({d.date for d in days if d.date})
    msg = (f"已导入 {day_count} 天到日程：新增 {len(result['created'])} 项、"
           f"更新 {len(result['updated'])} 项、保留已打卡 {len(result['kept'])} 项、"
           f"清理 {result['removed']} 项")
    if shifted["shift_days"]:
        msg = (f"方案日期已过，已顺延 {shifted['shift_days']} 天到 "
               f"{shifted['to_date']} 起；" + msg)
    if activated["archived_count"]:
        msg += f"；{activated['archived_count']} 份旧方案已归档到历史方案"
    if activated.get("reactivated"):
        msg = "该方案已重新启用为进行中；" + msg
    return {**result, "day_count": day_count, "message": msg,
            "shift": shifted, "activated": activated}


@router.get("/{plan_id}")
def get_plan(plan_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """方案详情（含 7 天明细）"""
    plan = db.get(DietPlan, plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="方案不存在")
    return plan_dict(plan, with_days=True, db=db)


def _targets_for(plan: DietPlan, day: PlanDay, override: dict | None = None) -> dict:
    """计划级目标 + 当日覆盖 + 本次提交的覆盖"""
    merged = dict(plan.targets or {})
    merged.update(day.targets or {})
    if override:
        merged.update({k: v for k, v in override.items() if v is not None})
    return merged


def _apply_meals(plan: DietPlan, day: PlanDay, meals_in: list, rebalance: str | None,
                 targets: dict) -> tuple[dict, dict, dict | None]:
    """改菜 → 重算营养 → 按需处置超限；返回 (重算结果, 评估结果, 运动追加明细)"""
    meals = normalize_meals([m.model_dump() for m in meals_in])
    recalculated = meal_recalc.recalc_meals(meals)
    info = meal_recalc.evaluate(recalculated["total"], targets)
    exercise = None

    if info["status"] == "high" and rebalance == "portion":
        scaled = meal_recalc.apply_portion_rebalance(
            recalculated["meals"], info["portion_factor"])
        recalculated = meal_recalc.recalc_meals(scaled)
        info = meal_recalc.evaluate(recalculated["total"], targets)
    elif info["status"] == "high" and rebalance == "exercise":
        exercise = plan_lifecycle.rebalance_by_exercise(plan, day, info["excess_kcal"])

    return recalculated, info, exercise


def _nutrition_payload(recalculated: dict, info: dict, targets: dict,
                       exercise: dict | None = None) -> dict:
    return {
        "meals": recalculated["meals"],
        "total": recalculated["total"],
        "coverage": recalculated["coverage"],
        "status": info["status"],
        "warnings": info["warnings"],
        "portion_factor": info["portion_factor"],
        "target": {
            "calorie_min": targets.get("calorie_min"),
            "calorie_max": targets.get("calorie_max"),
            "protein_min_g": targets.get("protein_min_g"),
        },
        "exercise_added": exercise,
    }


@router.post("/{plan_id}/days/{day_index}/preview")
def preview_plan_day(
    plan_id: int,
    day_index: int,
    data: PlanDayPreviewIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """改菜试算（不落库）：边改边看热量/蛋白是否落在目标区间"""
    plan = db.get(DietPlan, plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="方案不存在")
    day = (db.query(PlanDay)
           .filter(PlanDay.plan_id == plan.id, PlanDay.day_index == day_index).first())
    if not day:
        raise HTTPException(status_code=404, detail="方案天不存在")

    targets = _targets_for(plan, day)
    recalculated, info, exercise = _apply_meals(plan, day, data.meals, data.rebalance, targets)
    payload = _nutrition_payload(recalculated, info, targets, exercise)
    payload["exercise_suggestion"] = plan_lifecycle.suggest_exercise(
        plan, day, info["excess_kcal"]) if info["status"] == "high" else {"added_min": 0}
    return payload


@router.put("/{plan_id}/days/{day_index}")
def update_plan_day(
    plan_id: int,
    day_index: int,
    data: PlanDayUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """编辑方案某一天：菜品 / 运动 / 睡眠 / 饮水 / 当日目标。

    菜品改动会重算这一天所有营养并写一份快照，日程里的待办内容实时跟着变。
    """
    plan = db.get(DietPlan, plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="方案不存在")
    day = (db.query(PlanDay)
           .filter(PlanDay.plan_id == plan.id, PlanDay.day_index == day_index).first())
    if not day:
        raise HTTPException(status_code=404, detail="方案天不存在")

    targets = _targets_for(plan, day, data.targets)
    nutrition_payload = None

    if data.theme is not None:
        day.theme = data.theme
    if data.meals is not None:
        recalculated, info, exercise = _apply_meals(
            plan, day, data.meals, data.rebalance, targets)
        day.meals = recalculated["meals"]
        day.estimated_calories = round(recalculated["total"]["calories"])
        targets["nutrition_snapshot"] = meal_recalc.summarize(
            recalculated["total"], targets, info)
        targets["calorie_source"] = "manual_edit"
        nutrition_payload = _nutrition_payload(recalculated, info, targets, exercise)
    if data.exercise is not None:
        day.exercise = data.exercise.model_dump()
        if not data.exercises:
            # 只传主项时，运动处方退化为「这一项」，避免和 exercises 不一致
            day.exercises = [day.exercise]
    if data.exercises is not None:
        items = [item.model_dump() for item in data.exercises]
        day.exercises = items
        day.exercise = items[0] if items else None
    if data.sleep is not None:
        day.sleep = data.sleep
    if data.water_ml is not None:
        day.water_ml = data.water_ml

    day.targets = targets
    if data.meals is None:
        day.estimated_calories = round(sum((m.get("calories") or 0) for m in (day.meals or [])))
    # 留一条修改痕迹：评价里的「调整质量」用它判断用户改了多少、改完是否还在区间
    day.edit_count = (day.edit_count or 0) + 1
    day.last_edited_at = datetime.now()
    sync_plan_day_checkins(db, user.id, day)   # 已导入日程的该天任务同步
    db.commit()

    result = plan_day_dict(day)
    if nutrition_payload:
        result["nutrition"] = nutrition_payload
    return result


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
    """归档旧方案（停用即生成一份执行评价，留进历史方案）"""
    plan = db.get(DietPlan, plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="方案不存在")
    plan.status = "archived"
    plan_review.store_review(db, plan)
    db.commit()
    return {"ok": True, "status": plan.status, "review": plan.review}


@router.get("/{plan_id}/review")
def get_plan_review(plan_id: int, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """这一期的执行评价：打卡完成率 / 目标达成 / 连续坚持 / 调整质量。

    进行中的方案只算进度（final=False）；已经归档或已过结束日的算最终评价并落库。
    """
    plan = db.get(DietPlan, plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="方案不存在")

    last_day = (db.query(PlanDay).filter(PlanDay.plan_id == plan.id)
                .order_by(PlanDay.date.desc()).first())
    ended = bool(last_day and last_day.date and last_day.date < date.today())
    final = plan.status != "active" or ended

    if final and plan.review:
        return plan.review
    review = plan_review.build_review(db, plan, final=final)
    if final:
        plan.review = review
        db.commit()
    return review
