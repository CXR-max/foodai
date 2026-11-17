"""每日汇总：一次请求拿全「今天该多少 / 已经多少」

日程顶部的目标条、我的页的当日数据卡都读这个接口，
避免前端分别请求目标、饮食、打卡、饮水、睡眠再自己拼，数字对不上。
"""
from datetime import date
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.check_in import CheckIn
from app.models.diet_deviation import DietDeviation
from app.models.diet_plan import DietPlan
from app.models.user import User
from app.services import (adapt, day_context, day_mode, diet_intake, disposition,
                          loop_closure, metric_bands, recalc, settlement, wellness)
from app.services.meal_recalc import MEAL_LABELS

router = APIRouter(prefix="/day", tags=["每日汇总"])

logger = logging.getLogger("daily")


def _pct(value: float, target: float | None) -> int | None:
    if not target:
        return None
    return int(round(min(max(value / target, 0), 2) * 100))


@router.get("/summary")
def day_summary(
    date_str: str | None = Query(default=None, alias="date"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    day = date.fromisoformat(date_str) if date_str else date.today()
    targets = day_context.targets_for_day(db, user.id, day)
    plan_day = day_context.active_plan_day(db, user.id, day)
    plan = db.get(DietPlan, plan_day.plan_id) if plan_day else None

    # 动态调整（幂等）：昨天熬夜/这几天没动/水喝少了/吃多吃少/蛋白吃不够，把今天起的
    # 运动强度、睡眠建议、饮水与热量蛋白纤维目标跟着改——计划跟着人走，不是人跟着单子走。
    # 目标数字同步落库；整餐重排（求解器 3~8s/天）由 adapt.apply 自己踢到后台线程。
    adapt_result = {"applied": []}
    if plan_day is not None:
        try:
            adapt_result = adapt.apply(db, user.id, day)
            db.flush()
        except Exception:  # noqa: BLE001 —— 调整失败不阻塞日程页
            logger.exception("动态调整失败 user=%s day=%s", user.id, day)
            db.rollback()
            plan_day = day_context.active_plan_day(db, user.id, day)

    # 摄入口径：实际记录优先；已打卡但没记的餐次按方案菜品折算（见 services/diet_intake.py）
    intake = diet_intake.day_intake(db, user.id, day)

    tasks = (db.query(CheckIn)
             .filter(CheckIn.user_id == user.id, CheckIn.check_date == day).all())
    done_tasks = [c for c in tasks if c.status == "done"]
    exercise_tasks = [c for c in done_tasks if c.check_type == "exercise"]
    burn = {
        "exercise_kcal": round(sum(c.calories_burned or 0 for c in exercise_tasks)),
        "exercise_min": sum(c.duration_min or 0 for c in exercise_tasks),
        "times": len(exercise_tasks),
    }

    total_water = wellness.water_total(db, user.id, day)
    water_target = int(targets.get("water_min_ml") or 2000)
    sleep = wellness.sleep_dict(wellness.day_sleep(db, user.id, day))

    # 指标区间：热量按当日运动量浮动（训练日上限回补），其余按目标区间 + 浮动容忍判定
    # 例外场景（聚餐/生病/出差…）在这一层放宽口径，不放宽的项照旧判
    mode_effect = day_mode.effects(db, user.id, day)
    band_targets = day_mode.apply_to_targets(
        metric_bands.effective_calorie_band(targets), mode_effect,
        tdee=(targets.get("prescription") or {}).get("tdee"),
    )
    bands = metric_bands.build_bands(
        band_targets,
        {
            "calorie": intake["calories"] or None,
            "protein": intake["protein_g"] or None,
            "water": total_water or None,
            "sleep": (sleep or {}).get("duration_h"),
            "exercise": burn["exercise_kcal"] or None,
        },
        keys=["calorie", "protein", "water", "sleep", "exercise"],
    )

    calorie_min = targets.get("calorie_min")
    calorie_max = band_targets.get("calorie_max")   # 训练日上限已按消耗回补
    calorie_status = None
    if calorie_max:
        if intake["calories"] > calorie_max:
            calorie_status = "over"
        elif calorie_min and intake["calories"] >= calorie_min:
            calorie_status = "in_range"
        else:
            calorie_status = "under"

    # 饮食合并判定：热量 + 蛋白一次判完，给一行结论（蛋白不足不阻塞打卡、不弹窗）
    # 还有餐没打卡时只算"进行中"，不判"热量不足"——那只是还没吃完
    pending = [c for c in tasks if c.status == "pending"
               and (c.task_key or "").startswith("meal_")]
    pending_labels = [MEAL_LABELS.get((c.task_key or "").replace("meal_", ""), "")
                      for c in pending]
    diet = metric_bands.diet_summary(
        band_targets, intake["calories"] or None, intake["protein_g"] or None,
        pending_meals=len(pending), pending_labels=pending_labels,
        intake=intake,   # 带上方案内/方案外拆分，判定才能分清「方案错」和「自己多吃」
        mode_effect=mode_effect,
    )
    # 偏离落库（幂等 upsert）：结算时点之后才写，上午不写。
    # GET 里写库看着不干净，但这是「一个事实只有一条记录」的必要代价——
    # 让每个页面各自去写才会出现同一天三条矛盾记录。
    point = settlement.settlement_of(day)
    if point["settled"] and diet and diet.get("items"):
        disposition.record_deviations(db, user.id, day, intake, band_targets, diet)
        db.commit()
    # 汇总行判"进行中"时，下面单行也别标红：同一件事不能一处说进行中、一处说不足
    if diet and diet.get("status") == "in_progress":
        hint = metric_bands.pending_meal_hint(pending_labels)
        for band in bands:
            if band["key"] in ("calorie", "protein") and band["status"] == "under":
                band["status"] = "in_progress"
                band["status_text"] = "进行中"
                band["hint"] = hint

    return {
        "date": str(day),
        "has_plan": plan_day is not None,
        "plan": {
            "id": plan.id if plan else None,
            "title": plan.title if plan else None,
            "day_index": plan_day.day_index if plan_day else None,
            "theme": plan_day.theme if plan_day else None,
            "meals": plan_day.meals if plan_day else [],
            "exercises": (plan_day.exercises or ([plan_day.exercise] if plan_day.exercise else []))
            if plan_day else [],
            "sleep": plan_day.sleep if plan_day else None,
            "water_ml": plan_day.water_ml if plan_day else None,
        },
        "targets": targets,
        "bands": bands,
        "band_note": band_targets.get("calorie_band_note") or band_targets.get("day_mode_note"),
        # 结算时点：前端据此决定「能不能下结论」「还能不能处置」
        "settlement": point,
        "mode": ({**mode_effect} if mode_effect.get("mode") not in (None, "normal") else None),
        # 动态调整卡片：系统这轮自动改了什么、为什么（前端直接渲染，可一键撤销）
        "adapt": {
            "applied": adapt_result.get("applied") or [],
            "state": adapt_result.get("state") or {},
        },
        # 一行汇总：「热量 1860/1800~1950 · 蛋白 96/90~115 · 达标」
        "diet": diet,
        "intake": {**intake, "calorie_status": calorie_status},
        "burn": burn,
        "water": {
            "total_ml": total_water,
            "target_ml": water_target,
            "max_ml": targets.get("water_max_ml"),
            "pct": _pct(total_water, water_target),
            "achieved": metric_bands.achieved(targets, "water", total_water or None)[0],
        },
        "sleep": sleep,
        "tasks": {
            "total": len(tasks),
            "done": len(done_tasks),
            "pct": _pct(len(done_tasks), len(tasks)) if tasks else 0,
        },
        "progress": {
            "calorie_pct": _pct(intake["calories"], targets.get("calorie_target") or calorie_max),
            "protein_pct": _pct(intake["protein_g"], targets.get("protein_min_g")),
            "burn_pct": _pct(burn["exercise_kcal"], targets.get("exercise_burn_min")),
        },
    }


# ---------------- 偏离处置 / 例外场景 / 自学闭环 ----------------

class ModeIn(BaseModel):
    date: str | None = None
    mode: str = "normal"          # social / training / period / sick / travel / normal
    note: str | None = None


class DispositionIn(BaseModel):
    deviation_id: int
    kind: str                      # trim_remaining / today_exercise / next_days_trim / ...
    payload: dict | None = None


@router.get("/state")
def day_state(
    date_str: str | None = Query(default=None, alias="date"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """这天的完整状态：结算到哪一步 + 例外模式 + 待处置偏离 + 每个偏离能怎么做

    前端「超限应对框」只读这一个接口：拿到的选项已经是算好的动作
    （"把晚餐少排 380 kcal"而不是"建议减少摄入"），点了直接 POST /day/disposition。
    """
    day = date.fromisoformat(date_str) if date_str else date.today()
    return loop_closure.day_state(db, user.id, day)


@router.post("/mode")
def set_day_mode(
    data: ModeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """设置当天模式：聚餐 / 加练 / 生理期 / 生病 / 出差；传 normal 即清除"""
    day = date.fromisoformat(data.date) if data.date else date.today()
    if day > date.today():
        raise HTTPException(status_code=400, detail="只能标记今天或过去的日子")
    row = day_mode.set_mode(db, user.id, day, data.mode, data.note)
    db.commit()
    effect = day_mode.effects(db, user.id, day)
    return {"ok": True, "mode": day_mode.mode_dict(row), "effects": effect,
            "message": (effect.get("note_text") or "已恢复正常口径")}


@router.post("/disposition")
def apply_disposition(
    data: DispositionIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """执行一条处置：缩后面没吃的餐 / 当天加练 / 后续几天下调 / 不处置"""
    deviation = db.get(DietDeviation, data.deviation_id)
    if not deviation or deviation.user_id != user.id:
        raise HTTPException(status_code=404, detail="偏离记录不存在")
    point = settlement.settlement_of(deviation.dev_date)
    if not point["settled"]:
        raise HTTPException(status_code=400, detail=point["message"])

    targets = day_mode.apply_to_targets(
        metric_bands.effective_calorie_band(
            day_context.targets_for_day(db, user.id, deviation.dev_date)),
        day_mode.effects(db, user.id, deviation.dev_date),
        tdee=(day_context.targets_for_day(db, user.id, deviation.dev_date)
              .get("prescription") or {}).get("tdee"),
    )
    result = disposition.apply(db, user.id, deviation, data.kind, data.payload,
                              targets=targets)
    db.commit()
    return {"ok": True, **result}


class AdaptRevertIn(BaseModel):
    date: str | None = None


@router.post("/adapt/revert")
def revert_adaptation(
    data: AdaptRevertIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """撤销某天的动态调整：恢复调整前的目标/餐食/运动/睡眠/饮水快照"""
    day = date.fromisoformat(data.date) if data.date else date.today()
    try:
        result = adapt.revert_day(db, user.id, day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **result}


@router.get("/self-review")
def self_review(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """系统自省：连续偏离趋势 / 处置采纳率 / 例外占比 / 哪些手段对你有效

    这是给「系统」看的检讨，不是给用户打分。前端放在打卡页底部，可折叠。
    """
    data = loop_closure.self_review(db, user.id)
    db.commit()
    return data


@router.get("/recalc")
def recalc_preview(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """连续 3 天同向偏离 → 目标是否该重算（只算不改）"""
    return recalc.preview_nutrition_context(db, user.id)


@router.post("/recalc")
def recalc_apply(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """执行目标重算：不再继续压摄入，而是按实际摄入与体重变化反推 TDEE"""
    proposal = recalc.propose(db, user.id)
    if not proposal:
        return {"ok": False, "message": "还没有连续 3 天同向偏离，目标不用动"}
    result = recalc.apply_recalc(db, user.id, proposal)
    db.commit()
    return result
