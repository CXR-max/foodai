"""按实际执行状态，动态调整后续几天的膳食 / 运动 / 睡眠 / 饮水

产品口径（用户确认）：
- 计划不是排完就不管。昨天熬夜、这两天没动、水喝少了、蛋白吃不够，
  后面几天要跟着改，而不是继续照原单子往下走。
- 每条调整都要能说清「为什么改」，不能让用户莫名其妙发现菜变了、运动变轻了。
- 不做惩罚性补偿：漏练不加量补回来、某餐少吃不在下一餐硬塞。
  依据是运动营养与体重管理的通行做法——补偿性进食和报复性加练都会让
  执行率崩掉，宁可把目标调回可完成的水平。

调整依据：
- 睡眠不足 → 次日不排中等以上强度。睡眠限制会降低运动表现、抬高自感用力
  和受伤风险（ACSM 与睡眠剥夺相关研究的一致结论）；此时加练不如先补觉。
- 饮水不足 → 次日饮水量上调 10%（封顶 3000 ml）。蛋白质摄入偏高时再补 200 ml，
  因为蛋白质代谢的含氮废物需要足够水分排出。
- 蛋白/纤维长期低于目标 → 后续几天把目标下沿往上提，让重排后的餐食补回来；
  只提下沿不提上沿，避免又变成「硬凑蛋白」。
- 摄入热量持续超上限（吃多了）→ 后续几天热量区间整体下移，把多吃的量摊开，
  并按新区间重排整餐。摊开比单日硬饿可持续；地板 max(BMR, 维持×0.8)、
  单次下移不超过目标 10%（与处置 S2 同一条底线）。
- 摄入热量持续低于下限（吃少了）→ **不逼用户补吃**。连续吃不完说明方案
  排多了，把下沿校准到实际可执行的水平（同样不低于代谢地板）并按新食量
  重排——方案排多了就改方案，而不是让用户硬塞。
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models.check_in import CheckIn
from app.models.diet_plan import PlanDay
from app.models.food_record import FoodRecord
from app.models.sleep_log import SleepLog
from app.models.water_log import WaterLog
from app.services import plan_targets

logger = logging.getLogger("adapt")

LOOKBACK_DAYS = 3
SLEEP_SHORT_H = 1.0          # 比目标少 1 小时才算「睡不够」，少半小时不折腾
WATER_LOW_RATIO = 0.7
PROTEIN_SHORT_RATIO = 0.9
FIBER_SHORT_RATIO = 0.8
WATER_CAP_ML = 3000
PROTEIN_CAP_PER_KG = 2.0
EARLIEST_BEDTIME = "22:30"   # 睡眠建议最多提前到这里，再早用户也做不到

# ---- 摄入热量线（吃多了 / 吃少了）----
# 日均超上限 150+ kcal 才动：估算误差本来就有 ±10%，小偏差不值得折腾方案
CALORIE_OVER_TRIGGER = 150
# 连续低于下限 85% 才算吃少了：偶尔一天吃不完正常，连续才是方案偏大
CALORIE_UNDER_RATIO = 0.85
# 区间单次下移不超过目标的 10%（处置 S2 同款底线），每天至少动 50 kcal 才值得重排
BAND_SHIFT_CAP = 0.10
MIN_BAND_STEP = 50
OVER_SPREAD_DAYS = 2         # 超额摊到后面 2 天
UNDER_SPREAD_DAYS = 2


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _hhmm(text, default: str) -> str:
    """ bedtime 列是 DateTime（真实数据），历史 JSON 里可能存 "HH:MM" 字符串，都兼容"""
    if hasattr(text, "strftime"):
        return text.strftime("%H:%M")
    raw = str(text or "")[:5]
    if len(raw) == 5 and raw[2] == ":":
        return raw
    return default


def _shift_bedtime(bedtime: str, minutes_earlier: int) -> str:
    """把入睡时间往前挪，处理跨零点（00:30 往前 30 分钟 = 00:00）"""
    try:
        hour, minute = (int(part) for part in bedtime.split(":")[:2])
    except (ValueError, AttributeError):
        return bedtime
    total = (hour * 60 + minute - minutes_earlier) % (24 * 60)
    shifted = f"{total // 60:02d}:{total % 60:02d}"
    earliest = _shift_bedtime_floor()
    # 22:30 之前不再往前推：作息没法一步改到位，建议也给不出去的
    if _minutes(shifted) < _minutes(earliest) and _minutes(bedtime) >= _minutes(earliest):
        return earliest
    return shifted


def _shift_bedtime_floor() -> str:
    return EARLIEST_BEDTIME


def _minutes(hhmm: str) -> int:
    try:
        hour, minute = (int(part) for part in hhmm.split(":")[:2])
    except (ValueError, AttributeError):
        return 0
    return hour * 60 + minute


def _plan_day(db: Session, user_id: int, day: date) -> PlanDay | None:
    row = (
        db.query(PlanDay)
        .join(CheckIn, CheckIn.plan_day_id == PlanDay.id, isouter=True)
        .filter(CheckIn.user_id == user_id, CheckIn.check_date == day)
        .first()
    )
    if row:
        return row
    # 还没导入日程的方案天：按日期反查（同一用户可能有多份方案，取进行中的）
    from app.models.diet_plan import DietPlan

    return (
        db.query(PlanDay)
        .join(DietPlan, DietPlan.id == PlanDay.plan_id)
        .filter(DietPlan.user_id == user_id, DietPlan.status == "active", PlanDay.date == day)
        .first()
    )


def _profile_ctx(db: Session, user_id: int) -> dict:
    from app.models.health_profile import HealthProfile

    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user_id).first()
    plan = None
    if profile is None:
        from app.models.diet_plan import DietPlan

        plan = (
            db.query(DietPlan)
            .filter(DietPlan.user_id == user_id, DietPlan.status == "active")
            .order_by(DietPlan.id.desc())
            .first()
        )
    if plan is not None:
        return dict(plan.basis_snapshot or {})
    if profile is None:
        return {}
    return {
        "height_cm": profile.height_cm, "weight_kg": profile.weight_kg,
        "age": profile.age, "gender": profile.gender,
        "activity_level": profile.activity_level, "goal": profile.goal,
        "dietary_restrictions": profile.dietary_restrictions or [],
        "taste_preferences": profile.taste_preferences or [],
        "exercise_preferences": profile.exercise_preferences or [],
        "sleep_time": profile.sleep_time, "wake_time": profile.wake_time,
    }


# ---------- 体检 ----------

def diagnose(db: Session, user_id: int, day: date, lookback: int = LOOKBACK_DAYS) -> dict:
    """读最近 lookback 天的实际执行（不含 day 当天），返回可判定的状态。"""
    start = day - timedelta(days=lookback)
    profile = _profile_ctx(db, user_id)
    latest_targets = {}
    plan_day = _plan_day(db, user_id, day)
    if plan_day is not None:
        latest_targets = dict(plan_day.targets or {})

    # 睡眠
    sleeps = (
        db.query(SleepLog)
        .filter(SleepLog.user_id == user_id,
                SleepLog.sleep_date >= start, SleepLog.sleep_date < day)
        .all()
    )
    sleep_target = _num(profile.get("sleep_min_h") or latest_targets.get("sleep_min_h"), 7.0)
    sleep_hours = [
        _num(row.duration_min) / 60.0 for row in sleeps if _num(row.duration_min) > 0
    ]
    short_nights = [round(h, 1) for h in sleep_hours if h < sleep_target - SLEEP_SHORT_H]
    # 入睡晚于 23:30 也算熬夜，哪怕睡够了时长（作息相位后移）；
    # 23:00 整睡属于正常作息，不算
    late_nights = 0
    for row in sleeps:
        bed = _hhmm(row.bedtime, "")
        if bed and (_minutes(bed) >= 23 * 60 + 30 or _minutes(bed) <= 4 * 60):
            late_nights += 1

    # 饮水
    waters = (
        db.query(WaterLog)
        .filter(WaterLog.user_id == user_id,
                WaterLog.log_date >= start, WaterLog.log_date < day)
        .all()
    )
    total_water = sum(_num(row.ml) for row in waters)
    water_days = len({row.log_date for row in waters}) or lookback
    water_avg = total_water / max(water_days, 1)
    water_target = _num(latest_targets.get("water_min_ml"), 2000) or 2000

    # 运动完成度
    checks = (
        db.query(CheckIn)
        .filter(CheckIn.user_id == user_id, CheckIn.check_type == "exercise",
                CheckIn.check_date >= start, CheckIn.check_date < day)
        .all()
    )
    planned_days = len({row.check_date for row in checks})
    done_days = len({row.check_date for row in checks if row.status == "done"})
    burned = sum(_num(row.calories_burned) for row in checks if row.status == "done")
    burn_cap = _num(latest_targets.get("exercise_burn_max"), 0)

    # 摄入
    records = (
        db.query(FoodRecord)
        .filter(FoodRecord.user_id == user_id, FoodRecord.voided_at.is_(None),
                FoodRecord.eaten_date >= start, FoodRecord.eaten_date < day)
        .all()
    )
    eaten_days = len({row.eaten_date for row in records}) or 1
    calorie_avg = sum(_num(row.calories) for row in records) / eaten_days
    protein_avg = sum(_num(row.protein_g) for row in records) / eaten_days
    fiber_avg = sum(_num(row.fiber_g) for row in records) / eaten_days
    sodium_avg = sum(_num(row.sodium_mg) for row in records) / eaten_days
    cal_min = _num(latest_targets.get("calorie_min"))
    cal_max = _num(latest_targets.get("calorie_max"))

    return {
        "window": {"from": str(start), "to": str(day - timedelta(days=1)), "days": lookback},
        "sleep": {
            "target_h": sleep_target,
            "avg_h": round(sum(sleep_hours) / len(sleep_hours), 1) if sleep_hours else None,
            "logged_days": len(sleep_hours),
            "short_nights": short_nights,
            "late_nights": late_nights,
            "insufficient": bool(short_nights) or late_nights >= 2,
        },
        "water": {
            "target_ml": int(water_target), "avg_ml": int(water_avg),
            "ratio": round(water_avg / water_target, 2) if water_target else 1.0,
            "low": water_target > 0 and water_avg < water_target * WATER_LOW_RATIO,
        },
        "exercise": {
            "planned_days": planned_days, "done_days": done_days,
            "missed": planned_days - done_days,
            "burned": int(burned),
            "overreached": bool(burn_cap and burned > burn_cap * 1.2),
        },
        "intake": {
            "calorie_avg": int(round(calorie_avg)),
            "calorie_min": int(cal_min) if cal_min else None,
            "calorie_max": int(cal_max) if cal_max else None,
            # 吃多了/吃少了：与方案区间比，不是与单点目标比
            "over": bool(cal_max and calorie_avg > cal_max + CALORIE_OVER_TRIGGER),
            "under": bool(cal_min and calorie_avg < cal_min * CALORIE_UNDER_RATIO),
            "protein_avg_g": round(protein_avg, 1),
            "fiber_avg_g": round(fiber_avg, 1),
            "sodium_avg_mg": round(sodium_avg, 0),
            "logged_days": eaten_days,
        },
        "profile": profile,
        "targets": latest_targets,
    }


# ---------- 调整 ----------

def _band_floor(base_targets: dict) -> float:
    """热量地板：max(BMR, 维持热量×0.8)。与处置 _bmr_floor 同口径；缺数据用目标×0.9 兜底"""
    prescription = base_targets.get("prescription") or {}
    bmr = _num(base_targets.get("bmr")) or _num(prescription.get("bmr"))
    tdee = _num(base_targets.get("tdee")) or _num(prescription.get("tdee"))
    target = _band_target(base_targets)
    floor = 0.0
    if bmr:
        floor = max(floor, bmr)
    if tdee:
        floor = max(floor, tdee * 0.8)
    if not floor and target:
        floor = target * 0.9
    return floor


def _band_target(base_targets: dict) -> float:
    prescription = base_targets.get("prescription") or {}
    return _num(base_targets.get("calorie_target")) or _num(prescription.get("target"))


def _recovery_exercise(main: dict, weight: float, ratio: float = 0.6) -> dict:
    """把高强度主项降级成恢复日：时长按比例缩，强度 light，消耗重算"""
    kind = str(main.get("type") or "快走")
    # 睡眠不足时优先换成低冲击的散步/拉伸，别硬撑着跑
    if kind not in ("散步", "快走", "拉伸", "瑜伽"):
        kind = "散步"
    minutes = max(10, int(round(_num(main.get("duration_min"), 30) * ratio / 5.0) * 5))
    met = plan_targets._met_for(kind, "light")
    return {
        "type": kind,
        "duration_min": minutes,
        "calories_burned": int(round(met * minutes * weight / 60.0)) if weight else 0,
        "intensity": "light",
        "tips": "按恢复强度做，能正常说话；困就别硬撑，散步也算完成",
    }


def plan_day(db: Session, user_id: int, day_obj: PlanDay, state: dict) -> dict:
    """算出这一天要改什么；不改数据库，由调用方决定是否落库。"""
    profile = state.get("profile") or {}
    weight = _num(profile.get("weight_kg"), 65.0)
    base_targets = dict(day_obj.targets or {})
    targets = dict(base_targets)
    reasons: list[dict] = []

    exercises = [dict(item) for item in (day_obj.exercises or [])]
    if not exercises and day_obj.exercise:
        exercises = [dict(day_obj.exercise)]

    sleep = dict(day_obj.sleep or {})
    water = int(day_obj.water_ml or targets.get("water_min_ml") or 2000)

    # ---- 睡眠不足：今天不排中高强度 ----
    if state["sleep"]["insufficient"] and exercises:
        main = exercises[0]
        if str(main.get("intensity") or "light") in ("moderate", "intense"):
            exercises[0] = _recovery_exercise(main, weight)
            reason = (
                f"最近 {len(state['sleep']['short_nights'])} 晚睡眠不足"
                if state["sleep"]["short_nights"] else
                f"最近有 {state['sleep']['late_nights']} 晚熬夜"
            )
            reasons.append({
                "field": "exercise", "action": "降级为恢复日",
                "reason": f"{reason}，今天不排中等以上强度，先把作息拉回来",
            })

    # ---- 运动超额：今天该恢复 ----
    elif state["exercise"]["overreached"] and exercises:
        main = exercises[0]
        if str(main.get("intensity") or "light") in ("moderate", "intense"):
            exercises[0] = _recovery_exercise(main, weight, ratio=0.7)
            reasons.append({
                "field": "exercise", "action": "降级为恢复日",
                "reason": "上一轮运动消耗超过上限，今天安排恢复，避免连续高强度",
            })

    if exercises:
        burn = sum(_num(item.get("calories_burned")) for item in exercises)
        targets["exercise_burn_target"] = int(burn)

    # ---- 睡眠目标：连续不够就提前睡 ----
    if state["sleep"]["insufficient"]:
        bedtime = _hhmm(sleep.get("bedtime"), _hhmm(profile.get("sleep_time"), "23:00"))
        moved = _shift_bedtime(bedtime, 30)
        if moved != bedtime:
            sleep["bedtime"] = moved
            planned = sleep.get("planned_h")
            reasons.append({
                "field": "sleep", "action": f"入睡提前到 {moved}",
                "reason": "连续几天没睡够，先往前挪 30 分钟，比一次挪两小时更能坚持",
            })
            if planned:
                sleep["planned_h"] = round(_num(planned) + 0.5, 1)

    # ---- 饮水：喝少了上调，蛋白多了补水 ----
    if state["water"]["low"]:
        raised = min(WATER_CAP_ML, int(round(water * 1.1 / 100.0) * 100))
        if raised > water:
            water = raised
            reasons.append({
                "field": "water", "action": f"饮水目标提到 {raised} ml",
                "reason": (f"最近日均只喝 {state['water']['avg_ml']} ml，"
                           f"不到目标的 {state['water']['ratio'] * 100:.0f}%"),
            })
    protein_per_kg = state["intake"]["protein_avg_g"] / max(weight, 1)
    if protein_per_kg > 1.6:
        water = min(WATER_CAP_ML, water + 200)
        reasons.append({
            "field": "water", "action": f"饮水目标再补 200 ml（共 {water} ml）",
            "reason": f"蛋白摄入约 {protein_per_kg:.1f} g/kg，代谢废物需要更多水分排出",
        })

    # ---- 膳食：蛋白 / 纤维长期不够，把下沿提上来让重排补回去 ----
    protein_min = _num(base_targets.get("protein_min_g"))
    if protein_min and state["intake"]["logged_days"] >= 2:
        if state["intake"]["protein_avg_g"] < protein_min * PROTEIN_SHORT_RATIO:
            cap = PROTEIN_CAP_PER_KG * weight
            raised = int(min(cap, round(protein_min * 1.1)))
            if raised > protein_min:
                targets["protein_min_g"] = raised
                reasons.append({
                    "field": "diet", "action": f"蛋白下限提到 {raised} g",
                    "reason": (f"最近日均蛋白 {state['intake']['protein_avg_g']} g，"
                               f"低于目标下限 {int(protein_min)} g"),
                })

    fiber_min = _num(base_targets.get("fiber_min_g"))
    if fiber_min and state["intake"]["logged_days"] >= 2:
        if state["intake"]["fiber_avg_g"] < fiber_min * FIBER_SHORT_RATIO:
            raised = int(round(fiber_min + 5))
            targets["fiber_min_g"] = raised
            reasons.append({
                "field": "diet", "action": f"纤维下限提到 {raised} g",
                "reason": (f"最近日均纤维 {state['intake']['fiber_avg_g']} g，"
                           f"低于目标 {int(fiber_min)} g，重排时多加全谷和蔬菜"),
            })

    # ---- 膳食热量：吃多了摊到后面几天，吃少了把方案校准到可执行水平 ----
    # 区间先写进 targets（快路径，页面立即生效）；整餐重排由 replan_pending
    # 在后台按 needs_replan 标记补做——求解器一天 3~8s，不能挂在页面请求里。
    band_before = None
    cal_min = _num(base_targets.get("calorie_min"))
    cal_max = _num(base_targets.get("calorie_max"))
    if state["intake"]["logged_days"] >= 2 and cal_min and cal_max:
        avg = state["intake"]["calorie_avg"]
        floor = _band_floor(base_targets)
        target = _band_target(base_targets)
        shift_cap = target * BAND_SHIFT_CAP if target else None
        if avg > cal_max + CALORIE_OVER_TRIGGER:
            excess = avg - cal_max
            per_day = min(excess / OVER_SPREAD_DAYS, shift_cap) if shift_cap else excess / OVER_SPREAD_DAYS
            per_day = max(MIN_BAND_STEP, per_day)
            new_min = cal_min - per_day
            if floor:
                new_min = max(new_min, floor)
            new_max = max(cal_max - per_day, new_min + 100)
            if new_min < cal_min - 1:
                targets["calorie_min"] = int(round(new_min))
                targets["calorie_max"] = int(round(new_max))
                band_before = {"calorie_min": int(cal_min), "calorie_max": int(cal_max)}
                reasons.append({
                    "field": "diet", "action": (
                        f"后 {OVER_SPREAD_DAYS} 天热量区间下调到 "
                        f"{int(round(new_min))}~{int(round(new_max))} kcal"),
                    "reason": (f"最近日均摄入 {avg} kcal，超出上限 {int(cal_max)}——"
                               "多吃的部分摊到后面几天，按新区间重排（换菜调克重），"
                               f"每天最多动 {int(round(per_day))} kcal，不用哪天硬饿"),
                })
        elif avg < cal_min * CALORIE_UNDER_RATIO:
            gap = cal_min - avg
            per_day = min(gap / UNDER_SPREAD_DAYS, shift_cap) if shift_cap else gap / UNDER_SPREAD_DAYS
            per_day = max(MIN_BAND_STEP, per_day)
            new_min = cal_min - per_day
            if floor:
                if new_min < floor and avg >= floor:
                    new_min = floor        # 校准到地板为止，不再往下
                elif avg < floor:
                    new_min = None          # 摄入已低于代谢地板：方案不能再压热量
            if new_min and new_min < cal_min - 1:
                new_max = max(cal_max - per_day, new_min + 100)
                targets["calorie_min"] = int(round(new_min))
                targets["calorie_max"] = int(round(new_max))
                band_before = {"calorie_min": int(cal_min), "calorie_max": int(cal_max)}
                reasons.append({
                    "field": "diet", "action": (
                        f"后 {UNDER_SPREAD_DAYS} 天热量下沿校准到 {int(round(new_min))} kcal"),
                    "reason": (f"最近日均只吃 {avg} kcal，长期够不着 {int(cal_min)} 的下限——"
                               "方案按你实际吃得下的量重排，营养七项下限不动，也不逼你补吃"),
                })

    return {
        "day": str(day_obj.date),
        "day_index": day_obj.day_index,
        "exercises": exercises,
        "exercise": exercises[0] if exercises else None,
        "sleep": sleep,
        "water_ml": water,
        "targets": targets,
        "targets_override": {
            key: targets[key]
            for key in ("protein_min_g", "fiber_min_g", "calorie_min", "calorie_max")
            if key in targets and targets.get(key) != base_targets.get(key)
        },
        # 热量区间变了就必须连带重排整餐，否则「数字变了菜还是原量」，
        # 用户按方案吃完又会被判超——这正是旧版只改数字被看穿是空壳的原因
        "needs_replan": band_before is not None,
        "band_before": band_before,
        "changed": bool(reasons),
        "reasons": reasons,
    }


def plan(db: Session, user_id: int, from_day: date, days: int = 3,
         state: dict | None = None, lookback: int = LOOKBACK_DAYS) -> dict:
    """算出后续 days 天要怎么调；不改数据库。"""
    state = state or diagnose(db, user_id, from_day, lookback=lookback)
    wanted = {from_day + timedelta(days=i) for i in range(max(1, days))}
    day_objs = []
    for offset_day in sorted(wanted):
        obj = _plan_day(db, user_id, offset_day)
        if obj is not None:
            day_objs.append(obj)

    adjustments = [plan_day(db, user_id, obj, state) for obj in day_objs]
    return {
        "state": {key: value for key, value in state.items()
                  if key not in ("profile", "targets")},
        "adjustments": adjustments,
        "changed_days": [item["day"] for item in adjustments if item["changed"]],
    }


def apply(db: Session, user_id: int, from_day: date, days: int = 3,
          kick: bool = True) -> dict:
    """把动态调整落库到方案天（幂等，当天已应用过就跳过）。

    调整范围：运动处方（熬夜/超额 → 恢复日）、睡眠建议（入睡提前 30 分钟）、
    饮水目标（喝少了上调）、蛋白/纤维下沿（提上来）、热量区间
    （吃多了摊到后面几天 / 吃少了校准到实际食量）。
    目标数字是快路径，同步落库；整餐重排慢（求解器 3~8s/天），落 needs_replan
    标记后交给 kick_replan 的后台线程，页面请求不等。
    """
    state = diagnose(db, user_id, from_day)
    result = plan(db, user_id, from_day, days=days, state=state)
    today_str = str(date.today())
    applied: list[dict] = []
    needs_replan = False

    for adj in result["adjustments"]:
        if not adj["changed"]:
            continue
        obj = _plan_day(db, user_id, date.fromisoformat(adj["day"]))
        if obj is None:
            continue
        flags = dict(obj.day_flags or {})
        prev = flags.get("adapted") or {}
        if prev.get("applied_at") == today_str and not prev.get("reverted"):
            continue  # 今天已经按同一份执行数据调过，不再覆盖（用户手改也保护住）

        # 撤销凭据：改动前的完整快照，回滚时原样写回
        before = {
            "targets": dict(obj.targets or {}),
            "meals": obj.meals,
            "exercises": obj.exercises,
            "exercise": obj.exercise,
            "sleep": obj.sleep,
            "water_ml": obj.water_ml,
        }

        exercises = adj["exercises"]
        if exercises:
            burn = sum(_num(item.get("calories_burned")) for item in exercises)
            # 恢复日消耗低于原目标下限，判定区间也要跟着降——
            # 不能一边把运动改成散步、一边继续按原消耗判「运动不足」
            for key in ("exercise_burn_min", "exercise_burn_max"):
                old = _num(obj.targets.get(key))
                if old and burn < old:
                    adj["targets"][key] = int(burn)
        if adj["water_ml"] != obj.water_ml:
            adj["targets"]["water_min_ml"] = adj["water_ml"]

        obj.exercises = exercises
        obj.exercise = adj["exercise"]
        obj.sleep = adj["sleep"]
        obj.water_ml = adj["water_ml"]
        obj.targets = {**(obj.targets or {}), **adj["targets"]}
        flags["adapted"] = {
            "applied_at": today_str,
            "window": state.get("window"),
            "reasons": adj["reasons"],
            "before": before,
            "needs_replan": bool(adj.get("needs_replan")),
        }
        obj.day_flags = flags
        applied.append({"day": adj["day"], "reasons": adj["reasons"]})
        if adj.get("needs_replan"):
            needs_replan = True

    if applied:
        db.commit()
        logger.info("用户 %s 动态调整 %d 天: %s", user_id, len(applied),
                    [item["day"] for item in applied])
        if needs_replan and kick:
            kick_replan(user_id, from_day, days=days)
    return {"state": result["state"], "applied": applied}


# ---------- 后台重排与撤销 ----------

_REPLAN_INFLIGHT: set[tuple[int, str]] = set()
_REPLAN_LOCK = threading.Lock()


def replan_pending(db: Session, user_id: int, from_day: date, days: int = 3) -> list[dict]:
    """把标记了 needs_replan 的方案天按新区间重排整餐（同步版）。

    复用处置的 _rebuild_day：已吃过任何一餐的天不动（记录要对得上吃的），
    求解不出来返回 None，标记留在原地下次再试。测试直接调这个；生产由
    kick_replan 包一层后台线程。
    """
    from app.services import disposition

    replanned: list[dict] = []
    for offset in range(max(1, days)):
        target_day = from_day + timedelta(days=offset)
        obj = _plan_day(db, user_id, target_day)
        if obj is None:
            continue
        flags = dict(obj.day_flags or {})
        adapted = dict(flags.get("adapted") or {})
        if not adapted.get("needs_replan"):
            continue
        rebuilt = disposition._rebuild_day(
            db, user_id, obj, dict(obj.targets or {}),
            reason=f"动态调整后按新区间重排（{target_day}）")
        # _rebuild_day 会往 day_flags 里写 replanned 留痕，重新读一遍再叠 adapted，
        # 否则这里的赋值会把它的留痕覆盖掉
        flags = dict(obj.day_flags or {})
        adapted = dict(flags.get("adapted") or {})
        adapted["needs_replan"] = False
        adapted["replanned"] = bool(rebuilt)
        adapted["replanned_changed"] = (rebuilt or {}).get("changed") or []
        flags["adapted"] = adapted
        obj.day_flags = flags
        replanned.append({
            "day": str(target_day), "ok": bool(rebuilt),
            "changed": adapted["replanned_changed"],
        })
    if replanned:
        db.commit()
        logger.info("用户 %s 动态调整重排完成: %s", user_id,
                    [item["day"] for item in replanned])
    return replanned


def kick_replan(user_id: int, from_day: date, days: int = 3) -> None:
    """在后台线程跑重排；同一 (用户, 起点) 已在跑就不重复起线程。"""
    key = (user_id, str(from_day))
    with _REPLAN_LOCK:
        if key in _REPLAN_INFLIGHT:
            return
        _REPLAN_INFLIGHT.add(key)

    def _run() -> None:
        try:
            from app.db.session import SessionLocal
            db = SessionLocal()
            try:
                replan_pending(db, user_id, from_day, days=days)
            finally:
                db.close()
        except Exception:  # noqa: BLE001 —— 后台失败只记日志，别把线程带崩
            logger.exception("后台重排失败 user=%s from=%s", user_id, from_day)
        finally:
            with _REPLAN_LOCK:
                _REPLAN_INFLIGHT.discard(key)

    threading.Thread(target=_run, name=f"adapt-replan-{user_id}", daemon=True).start()


def revert_day(db: Session, user_id: int, day: date) -> dict:
    """撤销对某天的动态调整：把 before 快照原样写回，留痕标 reverted。"""
    obj = _plan_day(db, user_id, day)
    if obj is None:
        raise ValueError(f"{day} 没有对应的方案天")
    flags = dict(obj.day_flags or {})
    adapted = dict(flags.get("adapted") or {})
    before = adapted.get("before")
    if not before:
        raise ValueError(f"{day} 没有可撤销的动态调整")
    if adapted.get("reverted"):
        raise ValueError(f"{day} 的动态调整已经撤销过了")

    obj.targets = dict(before.get("targets") or {})
    if before.get("meals") is not None:
        obj.meals = before["meals"]
    if before.get("exercises") is not None:
        obj.exercises = before["exercises"]
    if before.get("exercise") is not None:
        obj.exercise = before["exercise"]
    if before.get("sleep") is not None:
        obj.sleep = before["sleep"]
    if before.get("water_ml") is not None:
        obj.water_ml = before["water_ml"]
    adapted["needs_replan"] = False
    adapted["reverted"] = True
    adapted["reverted_at"] = datetime.now().isoformat(timespec="seconds")
    flags["adapted"] = adapted
    obj.day_flags = flags
    db.commit()
    logger.info("用户 %s 撤销 %s 的动态调整", user_id, day)
    return {"day": str(day), "reverted": True}
