"""连续偏离 → 重算目标：不再往下压摄入

为什么必须有这一层：
「今天没吃够，明天再少吃点」这个逻辑重复三天，用户就进入了系统性的低摄入——
起因可能只是 TDEE 估高了（办公室坐一天 vs 每天走一万步，差 300~500 kcal），
也可能体重已经掉了 2 kg 但目标还按旧体重算。
这时候继续压摄入不是纠正偏差，是制造一个新问题。

所以触发条件写死为 **连续 3 天同向偏离**（同方向、且已定稿），一旦命中：

- 先用**能量平衡反推**真实 TDEE：`TDEE = 平均日摄入 + 体重变化×7700/天数`
  （有两次体重记录时用这个，它比任何公式都准，因为它就是这个人实际发生的事）
- 没有体重记录时退化为「活动水平上下调一档」重算，仍然不硬压热量
- 新目标仍然受 BMR 地板约束（目标热量不低于 BMR）

重算结果写进 `plan_adjustments(kind=recalc_target)`，并把新目标回写到档案与后续方案天。
用户看得到「为什么改」——这是 self-correction 能被信任的前提。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.diet_deviation import DietDeviation, PlanAdjustment
from app.models.health_profile import HealthProfile
from app.models.weight_log import WeightLog
from app.services import day_context, diet_intake, nutrition, settlement

# 连续几天同向算「趋势」（不是一次两次的偶然）
TREND_DAYS = 3
# 体重变化多少才算「体重变了」（家用体重秤的日间波动大概 ±0.5 kg）
WEIGHT_MOVE_KG = 0.8
# 1 kg 脂肪组织约 7700 kcal（营养学惯例值，和 plan_targets 的减重速度同源）
KCAL_PER_KG = 7700

ACTIVITY_ORDER = ("sedentary", "light", "moderate", "active", "very_active")


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _final_days(db: Session, user_id: int, within: int = 10) -> list[date]:
    """最近已定稿的日期（倒序），判趋势只认定稿的天"""
    today = date.today()
    out: list[date] = []
    for offset in range(1, within + 1):
        day = today - timedelta(days=offset)
        if settlement.is_final(day):
            out.append(day)
    return out


def trend(db: Session, user_id: int) -> dict:
    """最近几天是不是一直朝同一个方向偏。

    只看「硬偏离」（soft 是略偏，属于正常波动，不该触发重算）。
    返回方向（over/under/none）、连续天数、涉及项。
    """
    days = _final_days(db, user_id)[:TREND_DAYS]
    if len(days) < TREND_DAYS:
        return {"direction": "none", "streak": 0, "days": [], "nutrients": []}

    rows = (db.query(DietDeviation)
            .filter(DietDeviation.user_id == user_id, DietDeviation.dev_date.in_(days),
                    DietDeviation.severity == "hard")
            .all())
    by_day: dict[date, set[str]] = {d: set() for d in days}
    nutrients: set[str] = set()
    for row in rows:
        if row.dev_date in by_day:
            by_day[row.dev_date].add(row.kind)
            nutrients.add(row.nutrient)

    # 同一天既有超又有不足（比如热量超、纤维不足）——这时不算「同向」
    direction = "none"
    for day in days:
        kinds = by_day[day] - {"fiber", "sodium", "sugar"}
        if kinds == {"over"}:
            direction = "over" if direction in ("none", "over") else "mixed"
        elif kinds == {"under"}:
            direction = "under" if direction in ("none", "under") else "mixed"
        else:
            direction = "mixed"

    if direction == "mixed":
        return {"direction": "none", "streak": 0, "days": [str(d) for d in days],
                "nutrients": sorted(nutrients), "note": "这几天方向不一致，不判趋势"}
    return {
        "direction": direction,
        "streak": len(days) if direction != "none" else 0,
        "days": [str(d) for d in days],
        "nutrients": sorted(nutrients),
    }


def _avg_intake(db: Session, user_id: int, days: list[date]) -> float:
    if not days:
        return 0.0
    total = sum(_num(diet_intake.day_intake(db, user_id, d).get("calories")) for d in days)
    return total / len(days)


def _weight_points(db: Session, user_id: int, days: list[date]) -> tuple[float, float, int]:
    """趋势窗口内最早与最晚的体重（没有记录返回 (0, 0, 0)）"""
    if not days:
        return 0.0, 0.0, 0
    start, end = min(days) - timedelta(days=3), max(days) + timedelta(days=1)
    rows = (db.query(WeightLog)
            .filter(WeightLog.user_id == user_id,
                    WeightLog.recorded_date >= start, WeightLog.recorded_date <= end)
            .order_by(WeightLog.recorded_date.asc())
            .all())
    if len(rows) < 2:
        return 0.0, 0.0, len(rows)
    return _num(rows[0].weight_kg), _num(rows[-1].weight_kg), len(rows)


def estimate_tdee(db: Session, user_id: int, days: list[date]) -> dict:
    """用能量平衡反推真实 TDEE。

    原理：这段时间体重变了多少 = (吃进去 − 消耗掉) ÷ 7700。
    所以 消耗 = 平均日摄入 − 体重变化×7700/天数。
    比公式准：它用的是这个人真实吃了多少、真实重了多少。
    """
    avg_intake = _avg_intake(db, user_id, days)
    first, last, count = _weight_points(db, user_id, days)
    span = len(days) or 1
    if not (first and last):
        return {"tdee": 0, "method": "none", "avg_intake": round(avg_intake),
                "note": "最近没有两次体重记录，无法反推，按活动水平重算"}

    delta_kg = last - first
    tdee = avg_intake - (delta_kg * KCAL_PER_KG / span)
    return {
        "tdee": int(round(tdee)),
        "method": "energy_balance",
        "avg_intake": round(avg_intake),
        "weight_from": first, "weight_to": last,
        "weight_delta_kg": round(delta_kg, 1),
        "days": span,
        "note": (f"这 {span} 天平均吃 {round(avg_intake)} kcal，体重从 {first:g} 变成 {last:g} kg，"
                 f"反推维持热量约 {int(round(tdee))} kcal"),
    }


def propose(db: Session, user_id: int, *, now: datetime | None = None) -> dict | None:
    """检查是否需要重算目标；需要则返回提案（不落库，等用户确认或自动执行）"""
    now = now or datetime.now()
    t = trend(db, user_id)
    if t["direction"] == "none" or t["streak"] < TREND_DAYS:
        return None

    days = [date.fromisoformat(d) for d in t["days"]]
    estimate = estimate_tdee(db, user_id, days)
    current = day_context.profile_targets(db, user_id)
    if not current:
        return None

    old_target = _num(current.get("calorie_target"))
    old_max = _num(current.get("calorie_max"))
    bmr = _num(current.get("prescription", {}).get("bmr") if isinstance(current.get("prescription"), dict) else current.get("bmr"))
    bmr = bmr or _num((current.get("prescription") or {}).get("bmr"))

    new_target = old_target
    method = estimate["method"]
    reason_bits: list[str] = []

    if method == "energy_balance" and estimate["tdee"] > 0:
        # 用反推的 TDEE 重新套同一套处方逻辑：诉求/赤字比例不变，只换 TDEE
        intent = (current.get("intent") or current.get("goal") or "maintain")
        presc = current.get("prescription") or {}
        deficit = _num(presc.get("deficit_pct"))
        surplus = _num(presc.get("surplus_pct"))
        factor = 1 - deficit + surplus
        new_target = int(round(estimate["tdee"] * factor / 10.0) * 10)
        reason_bits.append(
            f"按你这 {estimate['days']} 天实际吃的和体重变化反推，维持热量约 {estimate['tdee']} kcal"
            f"（原按公式估 {current.get('tdee') or '—'} kcal）")
    else:
        # 没有体重数据：只调活动水平一档，不硬改数字
        profile = db.query(HealthProfile).filter(HealthProfile.user_id == user_id).first()
        if not profile:
            return None
        level = (profile.activity_level or "moderate").strip().lower()
        idx = ACTIVITY_ORDER.index(level) if level in ACTIVITY_ORDER else 2
        step = 1 if t["direction"] == "over" else -1
        idx = min(max(idx + step, 0), len(ACTIVITY_ORDER) - 1)
        profile.activity_level = ACTIVITY_ORDER[idx]
        new_target = int(round(_num(day_context.profile_targets(db, user_id).get("calorie_target"))))
        reason_bits.append(
            f"没有连续的体重记录，先按活动水平从「{level}」调成「{profile.activity_level}」重算")

    # 地板：重算后不得低于 BMR
    if bmr and new_target < bmr:
        new_target = int(round(bmr))
        reason_bits.append(f"已抬到基础代谢 {int(bmr)} kcal（不再往下压）")

    if abs(new_target - old_target) < 50:
        return None

    direction_text = "偏高" if t["direction"] == "over" else "偏低"
    return {
        "direction": t["direction"],
        "streak": t["streak"],
        "days": t["days"],
        "old_target": int(old_target),
        "new_target": int(new_target),
        "old_max": int(old_max) if old_max else None,
        "bmr": int(bmr) if bmr else None,
        "estimate": estimate,
        "reason": "；".join(reason_bits),
        "message": (
            f"连续 {t['streak']} 天{'超' if t['direction'] == 'over' else '低于'}目标，"
            f"更像是目标{'估高' if t['direction'] == 'over' else '估低'}了（{direction_text}），"
            f"不是你没做到。计划把每日目标从 {int(old_target)} 调到 {new_target} kcal，"
            "不再继续压摄入。"
        ),
    }


def apply_recalc(db: Session, user_id: int, proposal: dict, *, now: datetime | None = None) -> dict:
    """落地重算：写档案目标 + 后续方案天 + 处置记录"""
    now = now or datetime.now()
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user_id).first()
    if not profile:
        return {"ok": False, "message": "没有健康档案，无法重算"}
    profile.daily_calorie_target = proposal["new_target"]

    # 后续方案天的目标区间也一起改（已定稿的不动）
    from app.services import plan_targets as pt
    updated_days: list[dict] = []
    ctx = {
        "height_cm": profile.height_cm, "weight_kg": profile.weight_kg,
        "body_fat_pct": profile.body_fat_pct, "age": profile.age,
        "gender": profile.gender, "activity_level": profile.activity_level or "moderate",
        "goal": profile.goal or "maintain",
        "daily_target_calories": profile.daily_calorie_target,
        "sleep_time": profile.sleep_time, "wake_time": profile.wake_time,
    }
    rebuilt = pt.build_targets(ctx)
    for offset in range(0, 8):
        day = date.today() + timedelta(days=offset)
        if settlement.is_final(day):
            continue
        plan_day = day_context.active_plan_day(db, user_id, day)
        if not plan_day:
            continue
        t = dict(plan_day.targets or {})
        for key in ("calorie_min", "calorie_max", "protein_min_g", "protein_max_g"):
            if rebuilt.get(key):
                t[key] = rebuilt[key]
        t["calorie_band_note"] = f"已按最新目标重算（{proposal['reason']}）"
        plan_day.targets = t
        updated_days.append({"date": str(day), "calorie_min": t.get("calorie_min"),
                             "calorie_max": t.get("calorie_max")})

    row = PlanAdjustment(
        user_id=user_id, kind="recalc_target", scope="targets",
        payload={"old_target": proposal["old_target"], "new_target": proposal["new_target"],
                 "estimate": proposal["estimate"], "days": proposal["days"],
                 "updated_days": updated_days},
        reason=proposal["reason"], status="applied",
        effective_date=date.today(), applied_at=now,
    )
    db.add(row)
    db.flush()
    return {"ok": True, "adjustment_id": row.id, "updated_days": updated_days,
            "message": proposal["message"]}


def preview_nutrition_context(db: Session, user_id: int) -> dict:
    """给前端的自省卡：趋势 + 是否需要重算（不落库）"""
    t = trend(db, user_id)
    proposal = propose(db, user_id) if t["direction"] != "none" else None
    return {"trend": t, "proposal": proposal}
