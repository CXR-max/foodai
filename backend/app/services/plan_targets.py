"""计划目标体系：诉求 → 处方 → 目标区间 + 依据（全部本地计算，不调模型）

设计原则：
1. **一个数字只有一个来源**：目标与区间只在这里算，日程/运动/计划/评价都只读这一份
2. **诉求优先，身体数据只定强度**：用户选「健康饮食」就不设赤字，不因为 BMI 高就偷偷减脂；
   BMI / 体脂 / 年龄只决定「减多少、要不要劝一句」
3. **每个数字都能解释**：返回的 targets 里每个键旁边挂一条 `_source`
   （id / kind / label / basis），前端「依据」弹窗只读它；
   kind 三档：guideline=指南原文、derived=公式推导、heuristic=工程取值，
   heuristic 的文案禁止写「依据《XX》」

口径说明：
- 热量：TDEE × (1 ± 赤字/盈余)，下限不低于 BMR×1.1；区间 −10% ~ +5%，训练日上限按额外消耗 40% 回补
- 蛋白：**BMI≥24 用理想体重（22×身高²）**，BMI<24 用实际体重；
  系数 减脂 1.6 / 维持 1.2 / 增肌 1.8 / 健康饮食 1.2，上限 2.0/1.6/2.2/1.8；
  同时受 DRIs 绝对下限（男 65 / 女 55 g）与「蛋白供能比 ≤30%」约束
- 脂肪 20~30%E、碳水 45~55%E
- 纤维 25 g、添加糖 <25 g、钠 <2000 mg
- 饮水：体重×30 ml 起（≥1500），上限体重×40 ml（≤4000）
- 睡眠：7~9 h（区间判定见 services/metric_bands.py）
- 运动消耗：减脂按下限取赤字的 50%（150~400），上限取下限 1.8 倍（≤800）
- 步数：8000~15000 步
- 每周减重速度**现算**：赤字×7÷7700，不写死
"""
from __future__ import annotations

import logging

from app.services import nutrition
from app.services.llm.prompts import EXERCISE_METABOLIC

logger = logging.getLogger("plan_targets")

GOAL_LABELS = {"lose": "减脂", "maintain": "维持体重", "gain": "增肌", "healthy": "健康饮食"}
GOALS = tuple(GOAL_LABELS)

# 蛋白系数（与 planner.PROTEIN_FACTORS 保持一致）：g / kg 基准体重
PROTEIN_FACTORS = {"lose": 1.6, "maintain": 1.2, "gain": 1.8, "healthy": 1.2}
# 蛋白上限：吃太多也存不住
PROTEIN_MAX_FACTORS = {"lose": 2.0, "maintain": 1.6, "gain": 2.2, "healthy": 1.8}
# DRIs 2023 蛋白 RNI 绝对量（不随体重缩放的下限）
PROTEIN_FLOOR_G = {"male": 65, "female": 55}
PROTEIN_FLOOR_ELDER_G = {"male": 72, "female": 62}
# 蛋白供能比兜底：超过 30%E 说明总热量太低或蛋白给多了
PROTEIN_ENERGY_CAP = 0.30

# 每周训练天数（由 WHO 2020 每周 150~300 min 换算）
TRAIN_DAYS_PER_WEEK = {"lose": 5, "maintain": 4, "gain": 5, "healthy": 3}

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

# 理想体重的 BMI 基准（正常区间中位）
IBW_BMI = 22.0


def _num(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _tenth(value: float) -> int:
    """四舍五入到 10 的整数倍（热量展示用）"""
    return int(round(value / 10.0)) * 10


def _src(source_id: str, kind: str, label: str, basis: str) -> dict:
    """一条依据：id 对应 docs/nutrition-doctrine.md 的编号"""
    return {"id": source_id, "kind": kind, "label": label, "basis": basis}


def ideal_weight(height_cm: float) -> float:
    """理想体重 = BMI 22 × 身高(m)²（超重/肥胖者的蛋白基准）"""
    if not height_cm or height_cm <= 0:
        return 0.0
    return round(IBW_BMI * (height_cm / 100.0) ** 2, 1)


def _protein_floor(gender: str | None, age) -> int:
    table = PROTEIN_FLOOR_ELDER_G if (_num(age) and float(age) >= 65) else PROTEIN_FLOOR_G
    return table.get((gender or "").lower(), table["male"] if (gender or "").lower() != "female" else 55)


def build_prescription(profile_ctx: dict) -> dict:
    """诉求 → 策略。这是「为什么给你这个数」的唯一出口。

    返回的 prescription 同时写进 targets 与方案，前端直接渲染成处方卡。
    """
    weight = _num(profile_ctx.get("weight_kg"))
    height = _num(profile_ctx.get("height_cm"))
    age = profile_ctx.get("age")
    gender = (profile_ctx.get("gender") or "").strip().lower() or None
    activity = (profile_ctx.get("activity_level") or "moderate").strip().lower()
    intent = (profile_ctx.get("goal") or "maintain").strip().lower()
    if intent not in GOALS:
        intent = "maintain"

    bmi = nutrition.calc_bmi(weight or None, height or None) or 0.0
    body_fat = _num(profile_ctx.get("body_fat_pct"))
    bmr = nutrition.calc_bmr(weight or None, height or None, age, gender) or 0.0
    tdee = bmr * nutrition.ACTIVITY_FACTORS.get(activity, 1.55) if bmr else 0.0

    # 蛋白基准体重：超重/肥胖按理想体重，避免「越胖蛋白给越多」
    ibw = ideal_weight(height)
    basis_weight = weight
    basis_note = "按实际体重计算"
    if bmi >= 24 and ibw:
        basis_weight = ibw
        basis_note = f"按理想体重 {ibw:g} kg（BMI {IBW_BMI:g}）计算，超重时按实际体重给会偏高"

    effective = intent
    deficit = 0.0
    surplus = 0.0
    warnings: list[str] = []

    if intent == "lose":
        if bmi and bmi < 18.5:
            effective = "maintain"
            warnings.append(f"BMI {bmi:g} 已偏低：再减容易掉肌肉、影响内分泌，本期先按维持量吃")
        elif bmi >= 28:
            deficit = 0.20
        elif bmi >= 24:
            deficit = 0.15
        else:
            deficit = 0.10
            if nutrition.body_fat_high(body_fat, gender):
                deficit = 0.15
                warnings.append("体重在正常范围但体脂偏高：赤字小、蛋白高，优先力量训练")
            else:
                warnings.append("体重在正常范围：走慢减路线，重点看体脂和腰围，别只盯体重")
    elif intent == "gain":
        if bmi >= 28:
            surplus = 0.05
            warnings.append("超重状态下只给小幅盈余 + 力量训练，可以同时增肌减脂")
        else:
            surplus = 0.10
    elif intent == "healthy":
        deficit = 0.0
        warnings.append("不设减重目标：吃够维持量，体重可能自然小幅下降，但不承诺速度")

    # 目标热量：算得出 TDEE 就现算，算不出才用档案里的旧值兜底
    target = 0
    if tdee:
        target = nutrition.calc_daily_target(
            weight or None, height or None, age, gender, activity, intent, body_fat
        ) or 0
    if not target:
        target = int(round(_num(profile_ctx.get("daily_target_calories")) or 2000))

    # 每周变化按「实际目标 − TDEE」现算：BMR 下限介入时速度会自然变小
    weekly = ((target - tdee) * 7 / 7700.0) if tdee else 0.0
    rate = round(weekly, 1)
    if abs(rate) < 0.05:
        rate = 0.0
    if rate < 0:
        rate_text = f"约 {abs(rate):.1f} kg/周"
    elif rate > 0:
        rate_text = f"约 +{rate:.1f} kg/周"
    else:
        rate_text = "基本持平"

    focus_map = {
        "lose": ["每餐一份优质蛋白", "每周 2 次力量训练", "主食一半换杂粮或薯类"],
        "gain": ["训练后 1 小时内补蛋白+碳水", "碳水别怕吃，优先练后和早餐", "每周秤一次体重看趋势"],
        "healthy": ["盐 < 5 g", "添加糖 < 25 g", "蔬菜 500 g、深色过半", "每周 3 次中等强度运动"],
        "maintain": ["三餐规律、蛋白均匀分布", "每周至少 2 次力量训练"],
    }

    if tdee:
        if deficit:
            summary = (f"维持热量约 {int(round(tdee))} kcal，按 {round(deficit * 100)}% 赤字给到 "
                       f"{target} kcal，预期{rate_text}")
        elif surplus:
            summary = (f"维持热量约 {int(round(tdee))} kcal，按 {round(surplus * 100)}% 盈余给到 "
                       f"{target} kcal，预期{rate_text}")
        else:
            summary = f"维持热量约 {int(round(tdee))} kcal，直接吃够维持量，不设赤字"
    else:
        summary = f"按档案现有数据给到 {target} kcal；补全身高体重年龄后会更准"

    return {
        "intent": intent,
        "effective_intent": effective,
        "label": GOAL_LABELS[intent],
        "effective_label": GOAL_LABELS[effective],
        "strategy": "deficit" if deficit else ("surplus" if surplus else "maintain"),
        "deficit_pct": round(deficit, 2),
        "surplus_pct": round(surplus, 2),
        "bmi": bmi or None,
        "bmi_level": nutrition.bmi_level(bmi or None),
        "body_fat_pct": body_fat or None,
        "bmr": int(round(bmr)) if bmr else None,
        "tdee": int(round(tdee)) if tdee else None,
        "target": target,
        "rate_kg_week": rate,
        "rate_text": rate_text,
        "basis_weight_kg": round(basis_weight, 1) if basis_weight else None,
        "basis_weight_note": basis_note,
        "train_days": TRAIN_DAYS_PER_WEEK[effective],
        "focus": focus_map[effective],
        "warnings": warnings,
        "summary": summary,
    }


def build_targets(profile_ctx: dict) -> dict:
    """按档案 + 诉求算每日目标；同一个用户同一份档案多次生成结果一致。"""
    p = build_prescription(profile_ctx)
    weight = _num(profile_ctx.get("weight_kg"))
    height = _num(profile_ctx.get("height_cm"))
    age = profile_ctx.get("age")
    gender = (profile_ctx.get("gender") or "").strip().lower() or None
    effective = p["effective_intent"]
    target = p["target"]
    bmr = p["bmr"] or 0
    tdee = p["tdee"] or 0

    calorie_max = _tenth(target * 1.05)
    # 下限 = 目标 −10%；BMR×1.1 是不该长期跌破的地板，但地板最多只占到目标的 95%，
    # 否则 20% 赤字时区间会被压成一条线（1900~2010），判定反而失真
    calorie_min = _tenth(target * 0.90)
    if bmr:
        guard = _tenth(bmr * 1.1)
        calorie_min = min(max(calorie_min, guard), _tenth(target * 0.95))
    calorie_min = min(calorie_min, calorie_max - 100)

    # ---- 蛋白：基准体重 × 系数，再受绝对下限与供能比上限约束 ----
    basis = p["basis_weight_kg"] or weight
    basis = basis or weight
    protein_g = int(round(basis * PROTEIN_FACTORS[effective])) if basis else 0
    floor = _protein_floor(gender, age)
    protein_floor_used = False
    if protein_g and protein_g < floor:
        protein_g = floor
        protein_floor_used = True
    protein_max_g = int(round(basis * PROTEIN_MAX_FACTORS[effective])) if basis else int(round(protein_g * 1.4))
    protein_max_g = max(protein_max_g, protein_g + 10)
    # 蛋白供能比兜底：热量偏低时别让蛋白挤掉主食和脂肪
    energy_cap = int(round(target * PROTEIN_ENERGY_CAP / 4))
    protein_capped = False
    if protein_max_g > energy_cap:
        protein_max_g = max(protein_g, energy_cap)
        protein_capped = True
    protein_min_g = min(protein_g, protein_max_g)

    fat_min_g = int(round(target * 0.20 / 9))
    fat_max_g = int(round(target * 0.30 / 9))
    carb_min_g = int(round(target * 0.45 / 4))
    carb_max_g = int(round(target * 0.55 / 4))

    # 饮水：下限体重×30 ml；上限体重×40 ml（大量出汗日最多 4000 ml）
    water_ml = int(round(max(1500.0, (weight * 30 if weight else 0)) / 50.0)) * 50
    water_max_ml = int(round(min(4000.0, max(2500.0, (weight * 40 if weight else 0))) / 50.0)) * 50
    water_max_ml = max(water_max_ml, water_ml + 500)

    deficit_kcal = tdee * p["deficit_pct"] if tdee else 0.0
    if effective == "lose":
        burn_min = int(min(400.0, max(150.0, deficit_kcal * 0.5)))
    elif effective == "gain":
        burn_min = 200
    else:
        burn_min = 180
    # 运动上限：别把「多练」当目标，超过这个量恢复跟不上
    burn_max = int(min(800, round(burn_min * 1.8 / 10.0) * 10))

    bedtime = str(profile_ctx.get("sleep_time") or DEFAULT_BEDTIME)[:5]
    wake_time = str(profile_ctx.get("wake_time") or DEFAULT_WAKE_TIME)[:5]

    sources = {
        "calorie_target": _src(
            "E06" if p["deficit_pct"] else "E08",
            "derived",
            f"TDEE {tdee} kcal × (1{'-' if p['deficit_pct'] else '+'}{round(max(p['deficit_pct'], p['surplus_pct']) * 100)}%)",
            "《中国超重/肥胖医学营养治疗指南 2021》日减 500 kcal 的保守换算；健康饮食/维持不设赤字",
        ),
        "calorie_min": _src(
            "E10", "heuristic", f"目标 × 0.9 = {calorie_min} kcal",
            f"按常规取 −10% 弹性带；下限不低于 BMR×1.1（{_tenth(bmr * 1.1) if bmr else '—'} kcal）",
        ),
        "calorie_max": _src(
            "E10", "heuristic", f"目标 × 1.05 = {calorie_max} kcal",
            "按常规取 +5% 弹性带，训练日再按额外消耗 40% 回补",
        ),
        "protein_min_g": _src(
            "P01+P02", "derived",
            (f"理想体重 {basis:g} kg（BMI {IBW_BMI:g}）× {PROTEIN_FACTORS[effective]:g} g/kg"
             if p["bmi"] and p["bmi"] >= 24
             else f"实际体重 {basis:g} kg × {PROTEIN_FACTORS[effective]:g} g/kg（未超重，无需折算）")
            + (f"，抬到 DRIs 下限 {floor} g" if protein_floor_used else ""),
            "指南 2021 按理想体重 1.2~1.5 g/kg；ISSN 2017 给 1.4~2.0 g/kg。"
            "BMI≥24 时按理想体重算，避免越胖蛋白给越多",
        ),
        "protein_max_g": _src(
            "P05", "heuristic",
            f"{basis:g} kg × {PROTEIN_MAX_FACTORS[effective]:g} g/kg"
            + (f"，压到供能比 30% 上限 {energy_cap} g" if protein_capped else ""),
            "再多也存不住，只增加肾脏负担与热量",
        ),
        "fat_min_g": _src("C02", "derived", f"目标 × 20% ÷ 9 = {fat_min_g} g", "DRIs 2023 脂肪供能比 20~30%E"),
        "fat_max_g": _src("C02", "derived", f"目标 × 30% ÷ 9 = {fat_max_g} g", "DRIs 2023 脂肪供能比 20~30%E"),
        "carb_min_g": _src("C01", "derived", f"目标 × 45% ÷ 4 = {carb_min_g} g", "DRIs 2023 碳水供能比 50~65%E，此处取保守下沿"),
        "carb_max_g": _src("C01", "derived", f"目标 × 55% ÷ 4 = {carb_max_g} g", "DRIs 2023 碳水供能比 50~65%E"),
        "fiber_min_g": _src("C04", "guideline", "25 g", "《中国居民膳食指南 2022》膳食纤维 25~30 g"),
        "sugar_max_g": _src("M03", "guideline", "25 g", "膳食指南 2022 + WHO 2015 糖指南：不超 50 g，最好 25 g 以下"),
        "sodium_max_mg": _src("M02", "guideline", "2000 mg", "WHO 钠指南 + 膳食指南 2022：食盐 <5 g/d"),
        "water_min_ml": _src("L01", "derived", f"体重 × 30 ml = {water_ml} ml", "DRIs 2023 基础饮水 + 体重换算，不低于 1500 ml"),
        "water_max_ml": _src("L01", "derived", f"体重 × 40 ml = {water_max_ml} ml", "上限 4000 ml，防短时间大量饮水稀释电解质"),
        "sleep_min_h": _src("L02", "guideline", "7 h", "AASM/SRS 2015 共识：成人 7~9 h"),
        "sleep_max_h": _src("L02", "guideline", "9 h", "同上"),
        "exercise_burn_min": _src(
            "L06", "heuristic",
            f"赤字 {int(round(deficit_kcal))} kcal × 50% → {burn_min} kcal" if deficit_kcal else f"{burn_min} kcal",
            "ACSM 只给每周 200~300 min；具体 kcal 按 MET 换算，属工程取值",
        ),
        "exercise_burn_max": _src("L07", "heuristic", f"下限 × 1.8 = {burn_max} kcal", "超过这个量恢复跟不上，收益为负"),
        "exercise_days_per_week": _src(
            "L05", "derived", f"每周 {p['train_days']} 天",
            "WHO 2020：每周 150~300 min 中等强度 + ≥2 次力量",
        ),
        "steps_min": _src("L09", "heuristic", "8000 步", "常见实践取值，无国标原文"),
        "steps_max": _src("L09", "heuristic", "15000 步", "常见实践取值，过量步行不额外加分"),
    }

    return {
        "calorie_target": target,
        "calorie_min": calorie_min,
        "calorie_max": calorie_max,
        "protein_min_g": protein_min_g,
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
        "exercise_days_per_week": p["train_days"],
        "steps_min": 8000,
        "steps_max": 15000,
        "goal": effective,
        "intent": p["intent"],
        "tdee": p["tdee"],
        "weight_kg": weight or None,
        "bmi": p["bmi"],
        "body_fat_pct": p["body_fat_pct"],
        "basis_weight_kg": p["basis_weight_kg"],
        "prescription": p,
        "_source": sources,
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
