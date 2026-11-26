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
- 单日极端（今天一顿超上限 15% 且 ≥250 kcal）→ 不等 3 天均值，直接摊到
  明后两天。均值会被前几天拉平，只靠均值线等于对「今天吃爆」无感。
- 连续漏练 → 不补练、不把欠的量堆到下一次（那是依从性崩掉的头号原因），
  只把后续处方缩短到做得到的量。
- 钠 / 添加糖连续偏高 → 后面几天的菜换清淡口味（改选菜偏好，不改上限；
  上限一压就和膳食宝塔的奶/豆硬下限打架，直接判无解）。
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

# ---- 单日快通道 ----
# 3 天均值会被前两天拉平：「前天 1600、昨天 1650、今天 2900」均值 2050 看着还行，
# 但今天实打实超了 900。所以单日单独判一档，双门槛防抖（比例 + 绝对量都要过）。
TODAY_OVER_RATIO = 1.15
TODAY_OVER_ABS = 250
TODAY_UNDER_RATIO = 0.60      # 低于下限 60%：已结算才算，没吃完不判

# ---- 运动补偿（吃多了用运动抵一部分）----
# 不是惩罚性加练：只在没熬夜、近 3 天没超额时启用，单日封顶 80 kcal / 15%
COMP_EXERCISE_CAP_RATIO = 0.15
COMP_EXERCISE_CAP_KCAL = 80
COMP_EXERCISE_MIN_KCAL = 20

# ---- 连续漏练 ----
# 不补练（漏掉的量绝不堆到下一练），只把后续处方的量摊回可完成水平
MISSED_MIN_DAYS = 2
MISSED_SOFTEN_RATIO = 0.8

# ---- 钠 / 添加糖：连续偏高就换清淡的菜 ----
# 不硬改上限：钠上限一压就与膳食宝塔的奶/豆硬下限冲突，直接判无解
SODIUM_HIGH_RATIO = 1.2
SUGAR_HIGH_RATIO = 1.3
BIAS_MIN_LOGGED_DAYS = 2
BIAS_MAX_OFFSET = 1           # 只对明后两天启用，避免整周都变清淡


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

def _today_state(db: Session, user_id: int, day: date, targets: dict,
                 intake: dict | None = None) -> dict:
    """今天的实时摄入与结算状态（单日快通道用）

    为什么单独一段：diagnose 的回看窗口是 [day-lookback, day)，故意不含今天
    ——今天的饭还没吃完，放进去会把均值拉偏。但「今天一顿吃掉 3000 大卡」
    这种单日极端必须当天就响应，否则要等 3 天均值追上来，用户觉得系统瞎了。
    """
    if intake is None:
        try:
            from app.services import diet_intake
            intake = diet_intake.day_intake(db, user_id, day)
        except Exception:  # noqa: BLE001 —— 算不出来就当今天没数据，不影响主流程
            logger.exception("读取今日摄入失败 user=%s day=%s", user_id, day)
            intake = {}
    intake = intake or {}

    calories = _num(intake.get("calories"))
    cal_min = _num(targets.get("calorie_min"))
    cal_max = _num(targets.get("calorie_max"))

    settled = False
    try:
        from app.services import settlement
        settled = bool(settlement.settlement_of(day).get("settled"))
    except Exception:  # noqa: BLE001
        settled = False

    return {
        "calories": int(round(calories)),
        "protein_g": round(_num(intake.get("protein_g")), 1),
        "fiber_g": round(_num(intake.get("fiber_g")), 1),
        "sodium_mg": round(_num(intake.get("sodium_mg")), 0),
        "sugar_g": round(_num(intake.get("sugar_g")), 1),
        "calorie_min": int(cal_min) if cal_min else None,
        "calorie_max": int(cal_max) if cal_max else None,
        "settled": settled,
        "records": int(intake.get("records") or 0),
        # 双门槛：比例 + 绝对量都要过，防「多喝一杯奶就改三天方案」
        "over_extreme": bool(cal_max and calories > cal_max * TODAY_OVER_RATIO
                             and calories - cal_max >= TODAY_OVER_ABS),
        # 已结算且吃得太少：不动方案（方案没问题，是今天没吃），只提示
        "under_hard": bool(cal_min and settled and 0 < calories < cal_min * TODAY_UNDER_RATIO),
    }


def diagnose(db: Session, user_id: int, day: date, lookback: int = LOOKBACK_DAYS,
             today_intake: dict | None = None) -> dict:
    """读最近 lookback 天的实际执行（不含 day 当天），返回可判定的状态。

    today_intake：今天的实时摄入。day_summary 已经算过一遍，传进来避免重复查库；
    不传就自己算（预览接口等场景）。
    """
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
    # 没记录 ≠ 没喝：历史全空时不能按 0 ml 判「喝太少」，否则会给新用户编出
    # 「日均只喝 0 ml，不到目标的 0%」这种自相矛盾的理由，还顺手抬高目标
    water_logged = len({row.log_date for row in waters})
    water_days = water_logged or lookback
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
    sugar_avg = sum(_num(row.sugar_g) for row in records) / eaten_days
    cal_min = _num(latest_targets.get("calorie_min"))
    cal_max = _num(latest_targets.get("calorie_max"))
    sodium_max = _num(latest_targets.get("sodium_max_mg"))
    sugar_max = _num(latest_targets.get("sugar_max_g"))

    # 今日实时：3 天回看窗口不含今天，单日吃超了必须单独判，否则永远不响应
    today = _today_state(db, user_id, day, latest_targets, today_intake)

    return {
        "day": str(day),
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
            "logged_days": water_logged,
            "ratio": round(water_avg / water_target, 2) if water_target else 1.0,
            "low": (water_logged > 0 and water_target > 0
                    and water_avg < water_target * WATER_LOW_RATIO),
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
            "sugar_avg_g": round(sugar_avg, 1),
            # 连续 2 天偏高才换口味，单天重口味不折腾
            "sodium_high": bool(sodium_max and eaten_days >= BIAS_MIN_LOGGED_DAYS
                                and sodium_avg > sodium_max * SODIUM_HIGH_RATIO),
            "sugar_high": bool(sugar_max and eaten_days >= BIAS_MIN_LOGGED_DAYS
                               and sugar_avg > sugar_max * SUGAR_HIGH_RATIO),
            "logged_days": eaten_days,
        },
        # 今日实时（单日快通道）：包含今天已记录的摄入与结算状态
        "today": today,
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


def _soften_exercise(main: dict, weight: float, ratio: float) -> dict:
    """连续漏练后把处方量摊薄：强度不变，只缩时长

    为什么不直接下调消耗目标：目标降了但处方没变，等于「计划里写跑 40 分钟、
    判定按 25 分钟算」，两边对不上。先把处方缩短，判定区间由 apply 跟着 burn 走。
    """
    kind = str(main.get("type") or "快走")
    intensity = str(main.get("intensity") or "moderate")
    minutes = max(10, int(round(_num(main.get("duration_min"), 30) * ratio / 5.0) * 5))
    met = plan_targets._met_for(kind, intensity)
    return {
        **main,
        "duration_min": minutes,
        "calories_burned": int(round(met * minutes * weight / 60.0)) if weight else 0,
        "tips": "按这个量做就行，漏掉的不补；能连续做到比一次练狠更重要",
    }


def _compensation_exercise(weight: float, kcal: int) -> dict:
    """吃多了的补偿项：单独加一段快走，明确克数/分钟数，不是加练口号"""
    met = plan_targets._met_for("快走", "moderate")
    per_min = met * weight / 60.0 if weight else 0
    minutes = max(10, int(round((kcal / per_min if per_min else 0) / 5.0) * 5))
    burned = int(round(per_min * minutes)) if per_min else 0
    return {
        "type": "快走",
        "duration_min": minutes,
        "calories_burned": burned,
        "intensity": "moderate",
        "tips": "因为前面多吃了一部分，用这段快走抵一些；不想加练就在卡片里关掉",
        "compensation": True,
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
    calorie_over = False
    notes: list[str] = []
    floor = _band_floor(base_targets)
    target = _band_target(base_targets)
    shift_cap = target * BAND_SHIFT_CAP if target else None
    cal_min = _num(base_targets.get("calorie_min"))
    cal_max = _num(base_targets.get("calorie_max"))

    if state["intake"]["logged_days"] >= 2 and cal_min and cal_max:
        avg = state["intake"]["calorie_avg"]
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
                calorie_over = True
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

    # ---- 单日快通道：今天一顿吃爆了，不等 3 天均值追上来 ----
    if not calorie_over and cal_min and cal_max:
        today = state.get("today") or {}
        if today.get("over_extreme"):
            excess = _num(today.get("calories")) - cal_max
            per_day = min(excess / OVER_SPREAD_DAYS, shift_cap) if shift_cap else excess / OVER_SPREAD_DAYS
            per_day = max(MIN_BAND_STEP, per_day)
            new_min = max(cal_min - per_day, floor) if floor else cal_min - per_day
            new_max = max(cal_max - per_day, new_min + 100)
            if new_min < cal_min - 1:
                targets["calorie_min"] = int(round(new_min))
                targets["calorie_max"] = int(round(new_max))
                band_before = {"calorie_min": int(cal_min), "calorie_max": int(cal_max)}
                calorie_over = True
                reasons.append({
                    "field": "diet", "action": (
                        f"明后两天热量区间下调到 {int(round(new_min))}~{int(round(new_max))} kcal"),
                    "reason": (f"今天已经吃到 {today.get('calories')} kcal，超上限 "
                               f"{int(round(excess))}——不等 3 天均值，直接摊到后面两天，"
                               f"每天最多动 {int(round(per_day))} kcal"),
                })
        elif today.get("under_hard"):
            # 吃太少不改方案：方案没问题，是今天没吃。改了反而把方案越调越低
            notes.append("今天摄入不到下限的 60%，方案不往下压——问题是没吃，不是方案排多了")

    # ---- 连续漏练：不补练，把后续处方摊薄到做得到 ----
    # 依据：漏练后把欠的量堆到下一次，是运动依从性崩掉的头号原因。
    # 所以只缩处方量，不动强度，也不额外加「补练」任务。
    if (state["exercise"]["missed"] >= MISSED_MIN_DAYS and exercises
            and not state["sleep"]["insufficient"]):
        exercises = [_soften_exercise(item, weight, MISSED_SOFTEN_RATIO) for item in exercises]
        reasons.append({
            "field": "exercise", "action": "运动量摊薄 20%（先做到）",
            "reason": (f"最近 {state['exercise']['missed']} 天没练——漏掉的量不补，"
                       "把后面几天的处方缩短，先做到再谈加量"),
        })

    # ---- 吃多了：明后两天用运动抵一部分（可选、小步、有护栏）----
    compensate = bool(profile.get("compensate_by_exercise", True))
    if (calorie_over and exercises and compensate
            and not state["sleep"]["insufficient"]
            and not state["exercise"]["overreached"]
            and state["exercise"]["missed"] < MISSED_MIN_DAYS):
        cap_ratio = (target * COMP_EXERCISE_CAP_RATIO) if target else 0
        extra = int(min(COMP_EXERCISE_CAP_KCAL, cap_ratio))
        existing = sum(_num(item.get("calories_burned")) for item in exercises)
        if extra >= COMP_EXERCISE_MIN_KCAL:
            add = _compensation_exercise(weight, extra)
            if add["calories_burned"]:
                exercises.append(add)
                burn_min = _num(base_targets.get("exercise_burn_min"))
                burn_max = _num(base_targets.get("exercise_burn_max"))
                if burn_min:
                    targets["exercise_burn_min"] = int(burn_min + add["calories_burned"])
                if burn_max:
                    targets["exercise_burn_max"] = int(burn_max + add["calories_burned"])
                targets["exercise_compensation"] = add["calories_burned"]
                reasons.append({
                    "field": "exercise",
                    "action": f"明后两天各加走 {add['duration_min']} 分钟（约 {add['calories_burned']} kcal）",
                    "reason": ("多吃的那部分用运动抵一些，不用靠硬饿；"
                               "熬夜或已经练过量时不会给你加，幅度也封顶"),
                })
        elif existing:
            pass

    # ---- 钠 / 添加糖：连续偏高就让后面几天的菜清淡一点 ----
    # 不改上限：钠上限一压就和宝塔的奶/豆硬下限打架，直接判无解（踩过）。
    # 改的是选菜偏好——同类里优先低钠/低糖的条目。
    bias_changed = False
    try:
        from_day = date.fromisoformat(state.get("day") or str(day_obj.date))
    except ValueError:
        from_day = day_obj.date
    if (day_obj.date - from_day).days <= BIAS_MAX_OFFSET:
        bias: dict[str, str] = {}
        if state["intake"].get("sodium_high"):
            bias["sodium"] = "low"
        if state["intake"].get("sugar_high"):
            bias["sugar"] = "low"
        if bias:
            targets["flavor_bias"] = bias
            bias_changed = True
            parts = []
            if bias.get("sodium"):
                parts.append(f"日均钠 {int(state['intake']['sodium_avg_mg'])} mg 偏高")
            if bias.get("sugar"):
                parts.append(f"日均添加糖 {state['intake']['sugar_avg_g']} g 偏高")
            reasons.append({
                "field": "diet", "action": "后面几天的菜换成清淡口味",
                "reason": f"{'、'.join(parts)}——重排时同类里优先低钠低糖的菜，标准不降",
            })

    if exercises:
        targets["exercise_burn_target"] = int(sum(
            _num(item.get("calories_burned")) for item in exercises))

    needs_replan = band_before is not None or bias_changed
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
            for key in ("protein_min_g", "fiber_min_g", "calorie_min", "calorie_max",
                        "exercise_burn_min", "exercise_burn_max", "flavor_bias")
            if key in targets and targets.get(key) != base_targets.get(key)
        },
        # 热量区间变了就必须连带重排整餐，否则「数字变了菜还是原量」，
        # 用户按方案吃完又会被判超——这正是旧版只改数字被看穿是空壳的原因。
        # 清淡偏好同理：不重排的话偏好只写进 targets，菜一点没变。
        "needs_replan": needs_replan,
        "band_before": band_before,
        "changed": bool(reasons),
        "reasons": reasons,
        "notes": notes,
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


def preview(db: Session, user_id: int, day: date, days: int = 3,
            today_intake: dict | None = None) -> dict:
    """预告：跑一遍和 apply 完全相同的判定，但**不写库**。

    存在的意义：改之前得能告诉用户「因为什么、后面几天会变成什么样」。
    旧链路只有改完才说，用户看到菜变了也不知道为什么，干脆不看了。

    今天已经调过的直接回读留痕，不重新算：目标已经被改小一轮，再算一次会
    在新区间上继续下压（看上去像系统一天改两次），而且撤销之后会立刻反弹。
    """
    today_str = str(date.today())
    applied_actions: list[dict] = []
    applied = False
    reverted_today = False

    for offset in range(max(1, days)):
        obj = _plan_day(db, user_id, day + timedelta(days=offset))
        if obj is None:
            continue
        adapted = (obj.day_flags or {}).get("adapted") or {}
        if adapted.get("applied_at") != today_str:
            continue
        if adapted.get("reverted"):
            reverted_today = True
            continue
        applied = True
        for reason in adapted.get("reasons") or []:
            applied_actions.append({
                "day": str(obj.date),
                "field": reason["field"],
                "action": reason["action"],
                "reason": reason["reason"],
            })

    if applied or reverted_today:
        return {
            "day": str(day),
            "applied": applied,
            "changed": bool(applied_actions),
            "actions": applied_actions,
            "notes": (["今天这轮自动调整你已经撤销，今天不再自动重调；明天再按新的执行数据判"]
                      if reverted_today else []),
            "headline": "；".join(a["action"] for a in applied_actions[:2]),
            "state": {},
        }

    state = diagnose(db, user_id, day, today_intake=today_intake)
    result = plan(db, user_id, day, days=days, state=state)

    actions: list[dict] = []
    notes: list[str] = []
    for adj in result["adjustments"]:
        for reason in adj["reasons"]:
            actions.append({
                "day": adj["day"],
                "field": reason["field"],
                "action": reason["action"],
                "reason": reason["reason"],
            })
        notes.extend(adj.get("notes") or [])

    return {
        "day": str(day),
        "applied": False,
        "changed": bool(actions),
        "actions": actions,
        "notes": notes,
        "headline": "；".join(a["action"] for a in actions[:2]),
        "state": {key: value for key, value in state.items()
                  if key not in ("profile", "targets")},
    }


def apply(db: Session, user_id: int, from_day: date, days: int = 3,
          kick: bool = True, today_intake: dict | None = None) -> dict:
    """把动态调整落库到方案天（幂等，当天已应用过就跳过）。

    调整范围：运动处方（熬夜/超额 → 恢复日）、睡眠建议（入睡提前 30 分钟）、
    饮水目标（喝少了上调）、蛋白/纤维下沿（提上来）、热量区间
    （吃多了摊到后面几天 / 吃少了校准到实际食量）。
    目标数字是快路径，同步落库；整餐重排慢（求解器 3~8s/天），落 needs_replan
    标记后交给 kick_replan 的后台线程，页面请求不等。
    """
    state = diagnose(db, user_id, from_day, today_intake=today_intake)
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
        # 今天已经按同一份执行数据调过就不再覆盖（含用户主动撤销：撤销了就别再偷偷调回去）
        if prev.get("applied_at") == today_str:
            continue

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
