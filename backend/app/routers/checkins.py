"""打卡路由：日历任务体系（按完成度给积分的核心）

流程：方案天 --from-plan 导入--> 日历上出现 pending 任务 --> 逐项 complete 变 done
     每完成一项 +3 分；当天任务全部完成额外 +10 分
另支持手动运动/饮食打卡（均为每日首次 +2 分，防止刷分）
"""
import logging
import re
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.check_in import CheckIn
from app.models.diet_plan import DietPlan, PlanDay
from app.models.user import User
from app.schemas.checkin import FromPlanIn, ManualCheckInIn
from app.services import points
from app.services.achievements import calc_streak, unlock_all
from app.services.llm.prompts import build_checkin_praise_messages
from app.services.llm.provider import get_llm_provider

logger = logging.getLogger("checkins")

router = APIRouter(prefix="/check-ins", tags=["健康打卡"])

TASK_LABELS = {
    "meal_breakfast": "早餐", "meal_lunch": "午餐",
    "meal_dinner": "晚餐", "meal_snack": "加餐", "exercise": "运动",
}


def checkin_dict(c: CheckIn, plan_day: PlanDay | None = None) -> dict:
    """打卡/任务序列化；pending 任务附带方案里的餐食详情，方便前端展示"要吃什么\""""
    detail = None
    if plan_day and c.task_key:
        if c.task_key.startswith("meal_"):
            meal_type = c.task_key.replace("meal_", "")
            detail = next((m for m in (plan_day.meals or []) if m.get("meal_type") == meal_type), None)
        elif c.task_key == "exercise":
            detail = plan_day.exercise
    return {
        "id": c.id, "check_date": str(c.check_date), "check_type": c.check_type,
        "task_key": c.task_key, "label": TASK_LABELS.get(c.task_key, "手动记录"),
        "status": c.status, "points_awarded": c.points_awarded,
        "exercise_type": c.exercise_type, "duration_min": c.duration_min,
        "calories_burned": c.calories_burned, "note": c.note,
        "intensity": c.intensity, "met_used": c.met_used, "burn_detail": c.burn_detail,
        "plan_day_id": c.plan_day_id, "detail": detail,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _gen_ai_praise(db: Session, user: User, ctx: dict) -> str | None:
    """打卡后生成 AI 个性化点评（锦上添花，绝不阻塞打卡主流程）。

    返回规则：
    - 真模型成功 → 一段点评（亮点 + 建议）
    - 无 key / 真模型失败降级 mock / 任何异常 → None（前端不展示，打卡与积分照常）
    """
    try:
        # 补充用户目标与今日运动总消耗，让点评有数据可"点评"而不是空泛鼓励
        from app.models.health_profile import HealthProfile
        profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
        goal = {"lose": "减脂", "maintain": "维持体重", "gain": "增肌"}.get(
            getattr(profile, "goal", "") or "", "维持体重")
        today_burn = db.query(func.sum(CheckIn.calories_burned)).filter(
            CheckIn.user_id == user.id, CheckIn.check_type == "exercise",
            CheckIn.status == "done", CheckIn.check_date == date.today()).scalar() or 0
        ctx = {**ctx, "goal": goal, "today_burn": round(today_burn)}

        provider = get_llm_provider()
        text = provider.chat(build_checkin_praise_messages(ctx), max_tokens=160)
        # mock 的通用对话话术不适合打卡点评；真模型失败降级 mock 时同样不展示
        if getattr(provider, "active_name", "mock") == "mock":
            return None
        text = (text or "").strip()
        if not text or len(text) > 100:
            return None
        return text
    except Exception as e:  # noqa: BLE001 —— 点评是加分项，任何失败都静默跳过
        logger.warning("打卡 AI 点评生成失败：%s", e)
        return None


@router.get("")
def list_checkins(
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD 查单日"),
    month: str | None = Query(default=None, description="YYYY-MM 查月历打点"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查单日任务列表 或 月历完成度打点（二选一）"""
    if month:
        if not re.match(r"^\d{4}-\d{2}$", month):
            raise HTTPException(status_code=400, detail="month 格式应为 YYYY-MM")
        year, mon = int(month[:4]), int(month[5:7])
        start = date(year, mon, 1)
        end = date(year + (mon == 12), mon % 12 + 1, 1)
        rows = (db.query(CheckIn)
                .filter(CheckIn.user_id == user.id, CheckIn.check_date >= start, CheckIn.check_date < end)
                .all())
        by_date: dict[str, dict] = {}
        for c in rows:
            key = str(c.check_date)
            stat = by_date.setdefault(key, {"date": key, "total": 0, "done": 0})
            stat["total"] += 1
            stat["done"] += 1 if c.status == "done" else 0
        return {"month": month, "days": sorted(by_date.values(), key=lambda d: d["date"])}

    # 单日视图
    day = date.fromisoformat(date_str) if date_str else date.today()
    rows = (db.query(CheckIn)
            .filter(CheckIn.user_id == user.id, CheckIn.check_date == day)
            .order_by(CheckIn.created_at.asc()).all())
    # 批量取关联的方案天（展示待完成任务详情）
    plan_day_ids = {c.plan_day_id for c in rows if c.plan_day_id}
    plan_days = {d.id: d for d in db.query(PlanDay).filter(PlanDay.id.in_(plan_day_ids))} if plan_day_ids else {}
    done = sum(1 for c in rows if c.status == "done")
    return {
        "date": str(day),
        "items": [checkin_dict(c, plan_days.get(c.plan_day_id)) for c in rows],
        "summary": {"total": len(rows), "done": done,
                    "all_done": bool(rows) and done == len(rows)},
    }


@router.post("/from-plan")
def import_from_plan(
    data: FromPlanIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """把方案某天导入日历：为每餐+运动生成待完成任务。

    覆盖规则（解决"重新生成方案后导入，日历每天重复/内容一样"）：
    - 同一 (日期 + 任务) 已打卡(done) → 保留不动，绝不删除
    - 同一 (日期 + 任务) 未打卡(pending) → 覆盖为本次方案内容（指向新方案天）
    - 该日期上、方案里已不存在的旧 pending 任务 → 删除；done 保留
    """
    plan_day = db.get(PlanDay, data.plan_day_id)
    if not plan_day:
        raise HTTPException(status_code=404, detail="方案天不存在")
    plan = db.get(DietPlan, plan_day.plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="方案不存在")

    day = plan_day.date or date.today()
    desired: dict[str, str] = {}
    for meal in plan_day.meals or []:
        desired[f"meal_{meal.get('meal_type', 'snack')}"] = "diet"
    if plan_day.exercise:
        desired["exercise"] = "exercise"

    # 该日期上已有的任务（来自任意方案），按 task_key 归并
    existing = (db.query(CheckIn)
                .filter(CheckIn.user_id == user.id, CheckIn.check_date == day).all())
    by_key = {c.task_key: c for c in existing if c.task_key}

    created, updated, kept = [], [], []
    for key, ctype in desired.items():
        c = by_key.get(key)
        if c is None:
            db.add(CheckIn(user_id=user.id, check_date=day, check_type=ctype,
                           task_key=key, status="pending", plan_day_id=plan_day.id))
            created.append(key)
        elif c.status == "done":
            kept.append(key)          # 已打卡，保留
        else:
            c.plan_day_id = plan_day.id   # pending → 覆盖为本次方案
            c.check_type = ctype
            updated.append(key)

    # 删除该日期上、方案里已不存在且仍是 pending 的方案任务（done 一律保留）
    removed = 0
    for key, c in by_key.items():
        if key not in desired and c.status == "pending" and c.plan_day_id:
            db.delete(c)
            removed += 1

    db.commit()
    msg = (f"已导入 {len(created) + len(updated)} 项任务到日历"
           f"（新增 {len(created)}、更新 {len(updated)}、保留已打卡 {len(kept)}、清理 {removed}）")
    return {"created": created, "updated": updated, "kept": kept,
            "removed": removed, "message": msg}


@router.post("/{checkin_id}/complete")
def complete_checkin(
    checkin_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """完成任务打卡：pending → done，按完成度给分（单项+3，当日全清额外+10）"""
    c = db.get(CheckIn, checkin_id)
    if not c or c.user_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    if c.status == "done":
        raise HTTPException(status_code=400, detail="该任务已完成")
    # 防刷分：只能打卡当天，漏打的日子去积分商城用补签卡补
    if c.check_date != date.today():
        raise HTTPException(status_code=400,
                            detail="只能打卡今天；之前漏打的可在「积分商城」用补签卡补签")

    c.status = "done"
    # 运动任务没记录消耗时，从方案详情取类型/时长，再按个性化模型补算
    if c.check_type == "exercise" and not c.calories_burned and c.plan_day_id:
        pd = db.get(PlanDay, c.plan_day_id)
        if pd and pd.exercise:
            c.exercise_type = c.exercise_type or pd.exercise.get("type")
            c.duration_min = c.duration_min or pd.exercise.get("duration_min")
            if not c.calories_burned:
                from app.models.health_profile import HealthProfile
                from app.services.met import personalized_burn
                profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
                p = {col: getattr(profile, col) for col in
                     ("gender", "age", "weight_kg", "height_cm", "body_fat_pct", "activity_level")} if profile else {}
                r = personalized_burn(c.exercise_type, c.duration_min or 0, p, "moderate")
                c.calories_burned = r["calories_burned"]
                c.intensity = "moderate"
                c.met_used = r["met"]
                c.burn_detail = {"base": r["base"], "factors": r["factors"], "steps": r["steps"],
                                 "low": r["low"], "high": r["high"], "formula": r["formula"],
                                 "source": "方案任务按个性化模型补算"}
    # 注意：session 配置了 autoflush=False，先 flush 让下面的 pending 统计能看到本次改动
    db.flush()
    action = "task_exercise" if c.check_type == "exercise" else "task_meal"
    label = TASK_LABELS.get(c.task_key, "任务")
    pts = points.award(db, user, action, description=f"完成方案任务：{label}",
                       ref_type="checkin", ref_id=c.id) or 0
    c.points_awarded = pts

    # 当日任务全部完成 → 额外奖励 +10（每天只发一次）
    bonus = 0
    remaining = (db.query(CheckIn)
                 .filter(CheckIn.user_id == user.id, CheckIn.check_date == c.check_date,
                         CheckIn.status == "pending").count())
    if remaining == 0:
        bonus = points.award(db, user, "task_all_done_bonus",
                             description=f"{c.check_date} 方案任务全部完成 🎉",
                             once_per_day=True) or 0
    unlock_all(db, user)   # 打卡可能解锁"连续打卡"类成就
    db.commit()

    # AI 个性化点评（锦上添花，失败返回 None，不影响打卡结果）
    day_total = db.query(CheckIn).filter(
        CheckIn.user_id == user.id, CheckIn.check_date == c.check_date).count()
    day_done = db.query(CheckIn).filter(
        CheckIn.user_id == user.id, CheckIn.check_date == c.check_date,
        CheckIn.status == "done").count()
    ai_praise = _gen_ai_praise(db, user, {
        "action_text": f"完成方案任务：{label}",
        "streak": calc_streak(db, user.id),
        "done": day_done, "total": day_total,
        "all_done": day_done == day_total,
        "points": pts + bonus,
    })
    return {"id": c.id, "status": c.status,
            "points_awarded": pts + bonus, "bonus": bonus,
            "ai_praise": ai_praise,
            "message": f"任务完成 +{pts} 分" + (f"，当日全部完成 +{bonus} 分 🎉" if bonus else "")}


@router.post("")
def manual_checkin(
    data: ManualCheckInIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """手动打卡：运动 / 补记饮食（均为每日首次 +2 分，同一天再打只记录不给分）

    运动打卡未传 calories_burned 时，按个性化模型自动计算：
    MET（按强度档）× 体重 × 时长 ÷ 60 × 性别/年龄/体成分/体能系数，
    档案数据来自健康档案（缺啥按中性值兜底），计算明细一并落库可追溯。
    """
    from app.models.health_profile import HealthProfile
    from app.services.met import INTENSITY_ORDER, personalized_burn

    day = date.fromisoformat(data.check_date) if data.check_date else date.today()
    # 防刷分：只能打卡当天，漏打的日子去积分商城用补签卡补
    if day != date.today():
        raise HTTPException(status_code=400,
                            detail="只能打卡今天；之前漏打的可在「积分商城」用补签卡补签")

    burned = data.calories_burned
    intensity = data.intensity or "moderate"
    met_used = detail = None
    if data.check_type == "exercise" and burned is None:
        profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
        p = {c: getattr(profile, c) for c in
             ("gender", "age", "weight_kg", "height_cm", "body_fat_pct", "activity_level")} if profile else {}
        r = personalized_burn(data.exercise_type, data.duration_min or 0, p, intensity)
        burned = r["calories_burned"]
        met_used = r["met"]
        detail = {"base": r["base"], "factors": r["factors"], "steps": r["steps"],
                  "low": r["low"], "high": r["high"], "formula": r["formula"]}
    if intensity not in INTENSITY_ORDER:
        intensity = "moderate"

    c = CheckIn(
        user_id=user.id, check_date=day, check_type=data.check_type,
        task_key="manual_food" if data.check_type == "diet" else "manual_exercise",
        status="done",
        exercise_type=data.exercise_type, duration_min=data.duration_min,
        calories_burned=burned, intensity=intensity if data.check_type == "exercise" else None,
        met_used=met_used, burn_detail=detail, note=data.note,
    )
    db.add(c)
    ref = data.check_type   # 用 ref_type 区分饮食/运动的每日首次
    desc = ("手动记录饮食" if data.check_type == "diet"
            else f"手动运动打卡：{(data.exercise_type or '')}{(str(data.duration_min or '') + '分钟')}".strip())
    # 运动/饮食统一每日首次 +2，同一天再打卡只记录不给分（防刷）
    pts = points.award(db, user, "manual_checkin", once_per_day=True,
                       description=desc, ref_type=ref) or 0
    c.points_awarded = pts
    unlock_all(db, user)
    db.commit()
    db.refresh(c)

    ai_praise = _gen_ai_praise(db, user, {
        "action_text": desc,
        "streak": calc_streak(db, user.id),
        "points": pts,
    })
    return {**checkin_dict(c), "points_awarded": pts, "ai_praise": ai_praise}


@router.get("/burn-preview")
def burn_preview(exercise_type: str = Query(..., description="运动类型"),
                 duration_min: int = Query(..., gt=0, le=600),
                 intensity: str = Query(default="moderate", description="强度档 light/moderate/intense"),
                 user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """运动消耗预估（个性化版）：按档案的性别/年龄/体脂/体能修正，
    返回区间与逐层明细，供前端弹窗展示"这个数怎么来的"、允许手动微调"""
    from app.models.health_profile import HealthProfile
    from app.services.met import INTENSITY_ORDER, personalized_burn

    if intensity not in INTENSITY_ORDER:
        intensity = "moderate"
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    p = {c: getattr(profile, c) for c in
         ("gender", "age", "weight_kg", "height_cm", "body_fat_pct", "activity_level")} if profile else {}
    r = personalized_burn(exercise_type, duration_min, p, intensity)
    r["formula"] = f"{r['formula']} = {r['calories_burned']} kcal"
    return r


@router.delete("/{checkin_id}")
def delete_checkin(checkin_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """删除打卡/任务（积分不回收）"""
    c = db.get(CheckIn, checkin_id)
    if not c or c.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    db.delete(c)
    db.commit()
    return {"ok": True}
