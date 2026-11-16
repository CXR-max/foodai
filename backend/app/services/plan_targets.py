"""计划目标体系 + 运动处方 + 睡眠方案（全部本地计算，不调模型）

设计原则：
- 目标只算一次，写进 diet_plans.targets；需要逐天不同时写 plan_days.targets 覆盖
- 日程 / 运动 / 我的 三个模块都读这一份，不再各自定标准
- 运动、睡眠不交给大模型：规则可复现、不会因模型波动导致同一个人两次生成结果差很多，
  也不会拖慢生成速度（云端 7 天方案本来就慢）

口径说明（写进方案摘要，答辩时可直接讲）：
- 热量区间：目标值 ±5% / -10%，下限不低于基础代谢 BMR × 1.1；训练日上限按额外消耗的 40% 回补
- 蛋白：减脂 1.6 g/kg、维持 1.2、增肌 1.8（与求解器一致）；上限 2.0 / 1.6 / 2.2，再多也存不住
- 脂肪 20~30% 热量、碳水占剩余热量
- 纤维 25 g（《中国居民膳食指南 2022》25~30 g）
- 添加糖 < 25 g、钠 < 2000 mg（盐 < 5 g）
- 饮水：下限体重 × 30 ml（不低于 1500），上限体重 × 40 ml（不超过 4000）
- 睡眠：成人 7~9 h（区间判定，6.5 h 起算达标，见 services/metric_bands.py）
- 运动消耗：下限按热量缺口的 50% 估（150~400 kcal），上限取下限的 1.8 倍（不超过 800）
- 步数：8000~15000 步
- 所有指标的「区间 + 浮动容忍 + 判定」统一收在 services/metric_bands.py，前后端不再各写一套阈值
"""
from __future__ import annotations

import logging

from app.services import nutrition
from app.services.llm.prompts import EXERCISE_METABOLIC

logger = logging.getLogger("plan_targets")

# 蛋白系数（与 planner.PROTEIN_FACTORS 保持一致）
PROTEIN_FACTORS = {"lose": 1.6, "maintain": 1.2, "gain": 1.8}
# 蛋白上限：吃太多也存不住，按体重给一个「再多吃没必要」的天花板
PROTEIN_MAX_FACTORS = {"lose": 2.0, "maintain": 1.6, "gain": 2.2}

# 每周训练天数：减脂 5 天、维持 4 天、增肌 5 天（其余为恢复日）
TRAIN_DAYS_PER_WEEK = {"lose": 5, "maintain": 4, "gain": 5}

# 训练日模板（按 7 天一个循环；刻意不排成连续 5 天，给恢复留空隙）
TRAIN_PATTERN = {
    3: {1, 3, 5},
    4: {1, 2, 4, 6},
    5: {1, 2, 3, 5, 6},
}

DEFAULT_BEDTIME = "23:00"
DEFAULT_WAKE_TIME = "07:00"

# 强度档对应的 MET 缩放（与 services/met.py 的口径一致，方案只做「计划强度」）
INTENSITY_FACTOR = {"light": 0.8, "moderate": 1.0, "intense": 1.2}

RECOVERY_TYPES = ("瑜伽", "快走")


def _num(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def build_targets(profile_ctx: dict) -> dict:
    """按档案算出每日目标上下限；同一个用户多次生成结果一致。"""
    weight = _num(profile_ctx.get("weight_kg"))
    height = _num(profile_ctx.get("height_cm"))
    age = profile_ctx.get("age")
    gender = profile_ctx.get("gender")
    activity = (profile_ctx.get("activity_level") or "moderate").strip().lower()
    goal = (profile_ctx.get("goal") or "maintain").strip().lower()
    if goal not in PROTEIN_FACTORS:
        goal = "maintain"

    bmr = nutrition.calc_bmr(weight or None, height or None, age, gender) or 0
    tdee = bmr * nutrition.ACTIVITY_FACTORS.get(activity, 1.55) if bmr else 0

    target = _num(profile_ctx.get("daily_target_calories"))
    if not target:
        target = nutrition.calc_daily_target(
            weight or None, height or None, age, gender, activity, goal
        ) or 2000
    target = int(round(target))

    calorie_max = int(round(target * 1.05 / 10.0)) * 10
    calorie_min = int(round(target * 0.90 / 10.0)) * 10
    if bmr:
        # 低于基础代谢的「减脂」不可持续，抬到 BMR×1.1
        calorie_min = max(calorie_min, int(round(bmr * 1.1 / 10.0)) * 10)
    calorie_min = min(calorie_min, calorie_max - 100)

    protein_g = int(round(weight * PROTEIN_FACTORS[goal])) if weight else 0
    protein_max_g = int(round(weight * PROTEIN_MAX_FACTORS[goal])) if weight else int(round(protein_g * 1.4))
    fat_min_g = int(round(target * 0.20 / 9))
    fat_max_g = int(round(target * 0.30 / 9))
    carb_min_g = int(round(target * 0.45 / 4))
    carb_max_g = int(round(target * 0.55 / 4))

    # 饮水：下限体重×30 ml；上限体重×40 ml（大量出汗日最多到 4000 ml，再多会稀释电解质）
    water_ml = int(round(max(1500.0, (weight * 30 if weight else 0)) / 50.0)) * 50
    water_max_ml = int(round(min(4000.0, max(2500.0, (weight * 40 if weight else 0))) / 50.0)) * 50
    water_max_ml = max(water_max_ml, water_ml + 500)

    deficit = max(0.0, tdee - target) if tdee else 0.0
    if goal == "lose":
        burn_min = int(min(400.0, max(150.0, deficit * 0.5)))
    elif goal == "gain":
        burn_min = 200
    else:
        burn_min = 180
    # 运动上限：别把「多练」当目标，超过这个量恢复跟不上，收益反而是负的
    burn_max = int(min(800, round(burn_min * 1.8 / 10.0) * 10))

    bedtime = str(profile_ctx.get("sleep_time") or DEFAULT_BEDTIME)[:5]
    wake_time = str(profile_ctx.get("wake_time") or DEFAULT_WAKE_TIME)[:5]

    return {
        "calorie_target": target,
        "calorie_min": calorie_min,
        "calorie_max": calorie_max,
        "protein_min_g": protein_g,
        "protein_max_g": protein_max_g,
        "fat_min_g": fat_min_g,
        "fat_max_g": fat_max_g,
        "carb_min_g": carb_min_g,
        "carb_max_g": carb_max_g,
        "fiber_min_g": 25,
        "sugar_max_g": 25,
        "sodium_max_mg": 2000,
        "water_min_ml": water_ml,
        "water_max_ml": water_max_ml,
        "sleep_min_h": 7.0,
        "sleep_max_h": 9.0,
        "bedtime": bedtime,
        "wake_time": wake_time,
        "exercise_burn_min": burn_min,
        "exercise_burn_max": burn_max,
        "exercise_days_per_week": TRAIN_DAYS_PER_WEEK[goal],
        "steps_min": 8000,
        "steps_max": 15000,
        "goal": goal,
        "tdee": int(round(tdee)) if tdee else None,
    }


def _met_for(exercise_type: str, intensity: str) -> float:
    base = float(EXERCISE_METABOLIC.get(exercise_type, 5))
    return base * INTENSITY_FACTOR.get(intensity, 1.0)


def build_exercises(profile_ctx: dict, targets: dict, day_index: int) -> tuple[list[dict], dict]:
    """按「每周练几天 + 每天消耗下限」排运动处方，返回 (运动列表, 当日目标覆盖)。

    - 训练日：主项 + 拉伸，达到当日消耗下限
    - 恢复日：散步/瑜伽，只要下限的 30%，避免天天高强度
    """
    weight = _num(profile_ctx.get("weight_kg"))
    goal = targets.get("goal") or "maintain"
    daily_burn_min = int(targets.get("exercise_burn_min") or 180)
    train_days = int(targets.get("exercise_days_per_week") or 4)
    slot = (day_index - 1) % 7 + 1
    is_train = slot in TRAIN_PATTERN.get(train_days, TRAIN_PATTERN[4])

    preferences = [
        item for item in (profile_ctx.get("exercise_preferences") or [])
        if item in EXERCISE_METABOLIC
    ] or ["快走", "瑜伽", "骑行"]

    if is_train:
        # 第 1 周轻、第 2 周中、之后中~较高，按周次递进
        week = (day_index - 1) // 7
        intensity = "light" if week == 0 else ("moderate" if week == 1 else "intense")
        main_type = preferences[(day_index - 1) % len(preferences)]
        day_burn_min = daily_burn_min
    else:
        intensity = "light"
        main_type = "快走" if "快走" in preferences else preferences[0]
        day_burn_min = max(60, int(round(daily_burn_min * 0.3)))

    met = _met_for(main_type, intensity)
    if weight > 0 and met > 0:
        duration = day_burn_min / (met * weight / 60.0)
    else:
        duration = 30
    duration = int(round(min(max(duration, 10), 90) / 5.0) * 5)
    burned = int(round(met * duration * weight / 60.0)) if weight else 0

    main = {
        "type": main_type,
        "duration_min": duration,
        "calories_burned": burned,
        "intensity": intensity,
        "tips": (
            "先热身 5 分钟，结束后拉伸 5 分钟；心率保持「能说话但略喘」"
            if is_train else
            "今天以恢复为主，散步或拉伸即可，别做高强度"
        ),
    }
    stretch_min = 10 if is_train else 5
    stretch = {
        "type": "拉伸",
        "duration_min": stretch_min,
        "calories_burned": int(round(2.3 * stretch_min * weight / 60.0)) if weight else 0,
        "intensity": "light",
        "tips": "重点放松大腿后侧、小腿和腰背",
    }

    day_targets = dict(targets)
    day_targets["exercise_burn_min"] = day_burn_min
    day_targets["exercise_burn_max"] = int(min(800, round(day_burn_min * 1.8 / 10.0) * 10))
    day_targets["is_training_day"] = is_train
    day_targets["exercise_burn_target"] = burned + stretch["calories_burned"]
    return [main, stretch], day_targets


def _hours_between(bedtime: str, wake_time: str) -> float:
    """跨夜时长（只按 HH:MM 计算，不依赖时区）"""
    def minutes(text: str) -> int:
        try:
            hour, minute = str(text).split(":")[:2]
            return int(hour) * 60 + int(minute)
        except (ValueError, AttributeError):
            return 0

    bed, wake = minutes(bedtime), minutes(wake_time)
    if wake <= bed:
        wake += 24 * 60
    return round((wake - bed) / 60.0, 1)


def build_sleep(profile_ctx: dict, targets: dict, exercises: list[dict]) -> dict:
    """按作息 + 睡眠下限给当晚方案；高强度日提示提前入睡。"""
    bedtime = str(profile_ctx.get("sleep_time") or targets.get("bedtime") or DEFAULT_BEDTIME)[:5]
    wake_time = str(profile_ctx.get("wake_time") or targets.get("wake_time") or DEFAULT_WAKE_TIME)[:5]
    target_h = float(targets.get("sleep_min_h") or 7.0)
    planned_h = _hours_between(bedtime, wake_time)

    intense = any(e.get("intensity") in ("moderate", "intense") for e in exercises)
    tips = []
    if planned_h < target_h:
        tips.append(f"按现在的作息只有 {planned_h:g} 小时，建议提前 {int(round((target_h - planned_h) * 60))} 分钟上床")
    else:
        tips.append(f"作息稳定，可睡 {planned_h:g} 小时，落在 {target_h:g}~9 小时区间")
    if intense:
        tips.append("今天有中等以上强度运动，睡前 1 小时别刷手机，让核心体温降下来")
    tips.append("睡前 3 小时不再进食，避免夜间血糖波动")

    return {
        "target_h": target_h,
        "planned_h": planned_h,
        "bedtime": bedtime,
        "wake_at": wake_time,
        "intensity_day": bool(intense),
        "tips": "；".join(tips),
    }


def enrich_day(profile_ctx: dict, targets: dict, day_index: int) -> dict:
    """给单日补齐运动列表 / 睡眠方案 / 当日目标（模型只负责餐食，其余本地算）。"""
    exercises, day_targets = build_exercises(profile_ctx, targets, day_index)
    sleep = build_sleep(profile_ctx, day_targets, exercises)
    return {
        "exercises": exercises,
        "exercise": exercises[0] if exercises else None,
        "sleep": sleep,
        "targets": day_targets,
        "water_ml": int(day_targets.get("water_min_ml") or 2000),
    }
