"""超限处置：发现偏离 → 按时间给手段 → 落地 → 事后回看

这是「按方案吃完不该超」之后剩下的那一半问题：**真超了怎么办**。

三条硬原则：

1. **方案内超出不许让用户背**。`attribution=plan` 的偏离不进处置，
   进的是修复队列（生成侧继续收紧）——让用户为系统的错误少吃一顿，是最坏的产品行为。

2. **手段跟时间走**。同一个「超 400 kcal」，早上 9 点和晚上 9 点能做的事完全不同：
   - 上午：今天还有两餐，减后面两餐就行
   - 傍晚：只剩加餐，只能加练或顺延到明天
   - 21:30 之后：不许加练（影响睡眠，得不偿失），也不许补吃，只调后面几天
   所以 `options()` 必须接收结算时点，而不是给一套万能建议。

3. **每条手段都有安全底线**（见 SAFETY_RULES）。底线不是免责声明，是代码里真的会拒。
   一个会劝人「今天吃多了，明天别吃了」的系统，比不做处置更危险。

五种场景的处置方向：
- 额外加餐超了 → **默认不处置**。这是用户的主动选择，给一句"明天照常"就够。
  （不指责不是说不管，而是不把正常的社交吃喝当成失败）
- 没吃完低于下限 → 先问「吃不下还是没时间」，两个答案对应完全不同的手段：
  吃不下 → 减体积（换做法、缩份量、分次吃），总热量不动
  没时间 → 减餐次但不减总量（合并成一餐、换成便携食物）
- 方案内超了 → 修方案，不修用户
- 连续 3 天同向偏离 → 重算目标（TDEE 估错/体重变了），**不再继续压摄入**
- 生病/生理期 → 不处置
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.check_in import CheckIn
from app.models.diet_deviation import DietDeviation, PlanAdjustment
from app.models.diet_plan import DietPlan, PlanDay
from app.services import day_mode, diet_intake, nutrition, settlement
from app.services.meal_recalc import apply_portion_rebalance, recalc_meals

logger = logging.getLogger(__name__)

# ---------------- 五条安全底线 ----------------
# 每一条都对应一个真实会出事的场景，不是凑数
SAFETY_RULES = [
    {
        "id": "S1",
        "title": "运动不平账",
        "text": "加练最多抵掉超出量的一半，且不超当日运动上限；21:30 之后不再建议加练（影响睡眠）。"
                "吃多了靠猛练补，是把饮食问题变成运动损伤问题。",
    },
    {
        "id": "S2",
        "title": "下调有地板",
        "text": "后续几天的热量下限不低于 max(BMR, 维持热量×0.8)，单次下调不超过目标的 10%。"
                "底线是代谢，不是意志力。",
    },
    {
        "id": "S3",
        "title": "小额不结转",
        "text": "一次差额小于 300 kcal 不往后面摊——估算误差本来就有这么大，"
                "为几十千卡惦记三天，代价大于收益。",
    },
    {
        "id": "S4",
        "title": "补吃不新增餐次",
        "text": "补吃挂在已经存在的餐次上，不额外开一顿。"
                "否则会把「随时加餐」训练成习惯，反而更难控制总量。",
    },
    {
        "id": "S5",
        "title": "允许不处置",
        "text": "任何场景下「什么都不做」都是合法选项，且是额外加餐场景的默认推荐。"
                "偶尔一顿吃多不影响一周，反复自责才会让人放弃。",
    },
    {
        "id": "S6",
        "title": "不把一餐超的全压到下一餐",
        "text": "单餐最多缩 15%，超出的部分走加练或顺延到明天。"
                "把 400 kcal 全砍到晚餐，晚上会饿，然后报复性加餐——越处置越糟。",
    },
]

# 差额小于这个数不结转（S3）
CARRY_MIN_KCAL = 300
# 单次下调上限（S2）
MAX_TRIM_RATIO = 0.10
# 单餐最多缩这个比例（S6）：再往下就不是「少排一点」，是把一顿砍成半顿
MAX_MEAL_TRIM_RATIO = 0.15
# 运动最多抵扣超出量的比例（S1）
EXERCISE_COVER_RATIO = 0.5
# 21:30 之后不加练（S1）
LATE_HOUR_MIN = 21 * 60 + 30

# 处置窗口：跟时间走
WINDOW_SPECS = {
    "morning": {"label": "上午", "allow_trim": True, "allow_exercise": True,
                "allow_supplement": True, "note": "今天还有两餐以上，减后面的餐最省事"},
    "midday": {"label": "中午", "allow_trim": True, "allow_exercise": True,
               "allow_supplement": True, "note": "还剩晚餐，减晚餐就能拉回来"},
    "afternoon": {"label": "下午", "allow_trim": True, "allow_exercise": True,
                  "allow_supplement": True, "note": "只剩晚餐，减幅有限，可配合散步"},
    "evening": {"label": "晚上", "allow_trim": False, "allow_exercise": True,
                "allow_supplement": False, "note": "餐次基本吃完，能做的只有散步或顺延到明天"},
    "late": {"label": "深夜", "allow_trim": False, "allow_exercise": False,
             "allow_supplement": False, "note": "太晚了：加练影响睡眠、补吃影响血糖，只调明天"},
    "past": {"label": "已过去", "allow_trim": False, "allow_exercise": False,
             "allow_supplement": False, "note": "这天的餐次已经过去，只能调后面"},
}


def window_for(now: datetime | None = None, *, is_today: bool = True) -> str:
    """当前处置窗口（分钟级，不依赖时区）"""
    if not is_today:
        return "past"
    now = now or datetime.now()
    minutes = now.hour * 60 + now.minute
    if minutes < 11 * 60:
        return "morning"
    if minutes < 15 * 60:
        return "midday"
    if minutes < 19 * 60:
        return "afternoon"
    if minutes < LATE_HOUR_MIN:
        return "evening"
    return "late"


def _num(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


# ---------------- 偏离记录（事实层） ----------------

def record_deviations(db: Session, user_id: int, day: date, intake: dict, targets: dict,
                      diet: dict, *, now: datetime | None = None) -> list[DietDeviation]:
    """把当天判定结果落成偏离事实。

    只在「结算时点之后」写：上午不写（还没吃完，不是不足）。
    去重：同一天同一项同方向只留一条，重复调用更新数值而不是新增。

    soft 的判定：值落在浮动带内（不算错，只是略偏）→ soft，前端灰字提示；
    例外场景里被放宽的项 → soft，并在 note 里写清为什么。
    """
    from app.services import metric_bands

    now = now or datetime.now()
    point = settlement.settlement_of(day, now)
    if not point["settled"]:
        return []

    mode_effect = day_mode.effects(db, user_id, day)
    relaxed = set(mode_effect.get("relaxed_keys") or [])
    items = (diet or {}).get("items") or []
    out: list[DietDeviation] = []

    for item in items:
        status = item.get("status")
        if status not in ("over", "under"):
            continue
        key = item.get("key")
        if not key:
            continue
        # 半日快照只记「超」：不足是还没吃完，这时候记会天天误报
        if point["point"] == "midday" and status == "under":
            continue

        band = metric_bands.band_for(targets, key) or {}
        value = _num(item.get("value"))
        # 结构项（脂肪/碳水/纤维/钠/糖）没有浮动带，越界即 hard：
        # 「钠超 2000 mg」是个明确事实，不存在"略超也算达标"的余地
        if status == "over":
            marker = band.get("max") if band else item.get("max")
            severity = "soft" if (marker and band.get("soft_max") and value <= band["soft_max"]) else "hard"
        else:
            marker = band.get("min") if band else item.get("min")
            severity = "soft" if (marker and band.get("soft_min") and value >= band["soft_min"]) else "hard"
        if key in relaxed:
            severity = "soft"
        limit_value = item.get("max") if status == "over" else item.get("min")

        attribution = (diet or {}).get("attribution")
        if attribution not in ("plan", "extra", "portion", "intake", "user"):
            attribution = "user" if status == "over" else "intake"

        existing = (db.query(DietDeviation)
                    .filter(DietDeviation.user_id == user_id,
                            DietDeviation.dev_date == day,
                            DietDeviation.nutrient == key,
                            DietDeviation.kind == status)
                    .first())
        note_bits = []
        if key in relaxed:
            note_bits.append(f"{mode_effect.get('label')}：该项只提示不记偏离")
        row = existing or DietDeviation(user_id=user_id, dev_date=day, nutrient=key, kind=status)
        row.actual = round(value, 1)
        row.limit_value = limit_value
        row.delta = round(abs(_num(item.get("delta"))), 1)
        row.attribution = attribution
        row.source = attribution if attribution in ("plan", "extra", "portion") else "total"
        row.severity = severity
        row.settlement = point["point"]
        row.day_mode = mode_effect.get("mode")
        row.note = "；".join(note_bits) or None
        if not existing:
            row.status = "open"
            db.add(row)
        out.append(row)

    db.flush()
    return out


# ---------------- 处置选项（判断层） ----------------

def _bump_per_min(targets: dict) -> float:
    """每多走一分钟能多消耗多少（按快走 4.5 MET 估，有体重用体重）"""
    weight = _num(targets.get("weight_kg")) or 60.0
    return max(2.0, 4.5 * weight / 60.0)


def _burned_today(db: Session, user_id: int, day: date) -> float:
    rows = (db.query(CheckIn)
            .filter(CheckIn.user_id == user_id, CheckIn.check_date == day,
                    CheckIn.check_type == "exercise", CheckIn.status == "done")
            .all())
    return sum(_num(c.calories_burned) for c in rows)


def _remaining_meals(db: Session, user_id: int, day: date) -> list[str]:
    rows = (db.query(CheckIn.task_key)
            .filter(CheckIn.user_id == user_id, CheckIn.check_date == day,
                    CheckIn.status == "pending", CheckIn.task_key.like("meal_%"))
            .all())
    return [r[0].replace("meal_", "") for r in rows]


def _portion_delta(db: Session, user_id: int, day: date) -> float | None:
    """当天方案内记录里「比方案多盛/少盛」折算成热量（正=多了）；不显著时返回 None

    算法：逐条 `portion_delta_g × 该菜每克热量`。
    不用「方案内实际 − 方案基线」是因为那要重算一遍营养库，
    会和落库的数字出现两套；直接读记录上已经存好的差值，
    用户看到的克数差和系统用来处置的热量差永远对得上。
    """
    from app.models.food_record import FoodRecord
    from app.services import metric_bands

    rows = (db.query(FoodRecord)
            .filter(FoodRecord.user_id == user_id, FoodRecord.eaten_date == day,
                    FoodRecord.voided_at.is_(None),
                    FoodRecord.plan_portion_g.isnot(None),
                    FoodRecord.portion_delta_g.isnot(None))
            .all())
    if not rows:
        return None

    delta_kcal = 0.0
    baseline_kcal = 0.0
    for row in rows:
        portion_g = _num(row.portion_g)
        if portion_g <= 0:
            continue
        per_g = _num(row.calories) / portion_g
        delta_kcal += _num(row.portion_delta_g) * per_g
        baseline_kcal += _num(row.plan_portion_g) * per_g

    if not baseline_kcal:
        return None
    if abs(delta_kcal) < max(metric_bands.PORTION_MIN_KCAL,
                             baseline_kcal * metric_bands.PORTION_MIN_RATIO):
        return None
    return round(delta_kcal, 1)


def _bmr_floor(targets: dict) -> float:
    """热量地板：max(BMR, TDEE×0.8)。缺 BMR 时用目标值 90% 兜底，绝不等于 0。"""
    bmr = _num(targets.get("bmr")) or _num((targets.get("prescription") or {}).get("bmr"))
    tdee = _num(targets.get("tdee")) or _num((targets.get("prescription") or {}).get("tdee"))
    target = _num(targets.get("calorie_target"))
    floor = 0.0
    if bmr:
        floor = max(floor, bmr)
    if tdee:
        floor = max(floor, tdee * 0.8)
    if not floor and target:
        floor = target * 0.9
    return floor


# 补吃候选：轮着来。总是推「无糖酸奶 / 鸡蛋 / 牛奶」三样，用户第二次就不看了
SUPPLEMENT_POOL = {
    "protein": [
        ("无糖酸奶", "200 g"), ("鸡蛋", "2 个"), ("牛奶", "250 ml"), ("豆浆", "300 ml"),
        ("鸡胸肉", "80 g"), ("虾仁", "100 g"), ("豆腐", "150 g"), ("毛豆", "100 g"),
        ("水浸金枪鱼", "100 g"),
    ],
    "calorie": [
        ("香蕉", "1 根"), ("全麦面包", "1 片"), ("牛奶燕麦", "1 碗"), ("坚果", "一小把"),
        ("红薯", "1 个"), ("米饭", "半碗"),
    ],
}


def _supplement_suggestions(db: Session, user_id: int, day: date, nutrient: str,
                            count: int = 3) -> str:
    """补吃建议轮换：最近一周吃过的就不推，免得每天同一句"""
    recent = _recent_names(db, user_id, day + timedelta(days=1), 7)
    pool = SUPPLEMENT_POOL.get(nutrient) or SUPPLEMENT_POOL["protein"]
    fresh = [item for item in pool
             if not any(item[0] in name or name in item[0] for name in recent)]
    picked = (fresh or pool)[:count]
    return " / ".join(f"{name} {portion}" for name, portion in picked)


def options(db: Session, user_id: int, day: date, deviation: DietDeviation,
            targets: dict, *, now: datetime | None = None) -> list[dict]:
    """给一条偏离生成处置选项。

    返回值里每个选项自带：做什么、做多少、为什么、安全底线是否已介入。
    前端拿到的不是「建议多运动」这种废话，而是「加 25 分钟快走」这种能直接点的动作。
    """
    now = now or datetime.now()
    is_today = day == now.date()
    window = window_for(now, is_today=is_today)
    spec = WINDOW_SPECS[window]
    mode_effect = day_mode.effects(db, user_id, day)

    if mode_effect.get("no_disposition"):
        return [{
            "kind": "no_action",
            "label": "今天不处置",
            "detail": f"「{mode_effect.get('label')}」期间不推处置：先把饭吃下去、把水喝够，别的都不用管。",
            "reason": mode_effect.get("note_text") or "",
            "recommended": True,
            "safe": True,
            "safety": [],
        }]

    delta = _num(deviation.delta)
    nutrient = deviation.nutrient or "calorie"
    kind_over = deviation.kind == "over"
    attribution = deviation.attribution or ("user" if kind_over else "intake")
    # 归因给方案：不处置，进修复队列
    if attribution == "plan":
        return [{
            "kind": "fix_plan",
            "label": "这是方案排错了",
            "detail": "这天的餐食本身就在区间外，不是你的问题。已经记下来，下次生成会收紧；"
                      "你可以直接在方案里改掉这一餐。",
            "reason": "方案内超标归因于生成，不要求用户少吃",
            "recommended": True,
            "safe": True,
            "safety": [],
        }]

    opts: list[dict] = []
    remaining = _remaining_meals(db, user_id, day)
    per_min = _bump_per_min(targets)
    # 没给运动上限时按 300 kcal 兜底：S1 要的是「有额度」，不能因为字段缺就让加练永远不可用
    burn_cap_raw = _num(targets.get("exercise_burn_max")) or 300.0
    burn_cap = max(0.0, burn_cap_raw - _burned_today(db, user_id, day))
    # 还没吃的餐合计多少热量：S6 的上限按这个算，不是按超出量算
    remaining_kcal = 0.0
    portion_delta = _portion_delta(db, user_id, day)
    day_obj = _plan_day(db, user_id, day)
    if day_obj and remaining:
        per_meal = diet_intake.plan_meal_nutrition(day_obj)
        remaining_kcal = sum(_num((per_meal.get(m) or {}).get("calories")) for m in remaining)

    # ---- 0. 份量问题：先给「照克数盛」，这不是补偿而是下次不再多 ----
    # 份量吃多了，最有效的处置不是让他饿，是让他下次知道盛多少
    if kind_over and portion_delta and portion_delta > 0:
        opts.append({
            "kind": "portion_advice",
            "label": f"下一餐按方案克数盛（这餐多了 {int(round(portion_delta))} kcal）",
            "detail": "这餐的菜和方案对得上，多出来的是份量。下次主食按方案克数量一下"
                      "（比如 150 g 就是一小碗），菜不用减——份量问题改份量就够了。",
            "reason": "份量偏大可以矫正，不需要动方案，也不用饿着",
            "recommended": True,
            "safe": True,
            "safety": [],
            "payload": {"portion_delta": int(round(portion_delta))},
        })

    # ---- 1. 当天收口：减还没吃的餐（S6：单餐最多缩 15%）----
    if kind_over and nutrient == "calorie" and spec["allow_trim"] and remaining and delta >= 50:
        trim = min(delta, 650)
        safety: list[str] = []
        cap = remaining_kcal * MAX_MEAL_TRIM_RATIO if remaining_kcal else 0
        if cap and trim > cap:
            trim = cap
            safety.append(
                f"S6：单餐最多缩 {int(MAX_MEAL_TRIM_RATIO * 100)}%，"
                f"剩下的 {int(round(delta - trim))} kcal 走加练或顺延到明天"
            )
        trim = round(trim / 10.0) * 10
        labels = "、".join(diet_intake.MEAL_LABELS.get(m, m) for m in remaining)
        if trim >= 30:
            opts.append({
                "kind": "trim_remaining",
                "label": f"把{labels}少排 {int(trim)} kcal",
                "detail": f"还没吃的{labels}按这个量往下调：小量只缩主食克数，"
                          "要减得多就换个同类菜（饭换饭、菜换菜），餐次和菜数都不变。",
                "reason": f"超出 {int(delta)} kcal，从还没吃的餐里扣最省事，也不用额外动",
                "recommended": True,
                "safe": True,
                "safety": safety,
                "payload": {"trim_kcal": int(trim), "meals": remaining},
            })

    # ---- 2. 当天加练 ----
    if kind_over and nutrient == "calorie" and delta >= 50:
        covered = min(delta * EXERCISE_COVER_RATIO, burn_cap)
        minutes = int(round((covered / per_min) / 5.0) * 5)
        safety: list[str] = []
        if not spec["allow_exercise"]:
            safety.append("S1：21:30 之后不加练，影响睡眠得不偿失")
            minutes = 0
        elif covered < delta * EXERCISE_COVER_RATIO - 1:
            safety.append(f"S1：今天运动额度只剩 {int(burn_cap)} kcal，抵不满一半")
        if minutes >= 5:
            opts.append({
                "kind": "today_exercise",
                "label": f"加走 {minutes} 分钟（约抵 {int(covered)} kcal）",
                "detail": f"按快走算每分钟约 {per_min:.1f} kcal。"
                          f"只抵超出量的一半——吃多了靠猛练补，是把饮食问题变成运动损伤问题。",
                "reason": "不想改餐食时用，代价是花时间；抵不掉的另一半明天照常吃就行",
                "recommended": False,
                "safe": bool(safety) is False or minutes > 0,
                "safety": safety,
                "payload": {"minutes": minutes, "calories": int(covered)},
            })
        elif not spec["allow_exercise"]:
            opts.append({
                "kind": "today_exercise",
                "label": "加练（此时不建议）",
                "detail": "已经 21:30 之后，这会儿出去练会推后入睡、影响明天的食欲和状态。",
                "reason": safety[0],
                "recommended": False,
                "safe": False,
                "safety": safety,
                "payload": {"minutes": 0},
            })

    # ---- 3. 后续几天调整 ----
    if kind_over and nutrient == "calorie":
        if delta < CARRY_MIN_KCAL:
            opts.append({
                "kind": "no_action",
                "label": "不用往后摊",
                "detail": f"只超了 {int(delta)} kcal，还没到 {CARRY_MIN_KCAL} kcal 的结转门槛。"
                          "估算误差本来就有这么大，明天照常吃。",
                "reason": "S3：小额不结转",
                "recommended": True,
                "safe": True,
                "safety": ["S3：差额小于 300 kcal 不结转"],
            })
        else:
            floor = _bmr_floor(targets)
            base_min = _num(targets.get("calorie_min"))
            target = _num(targets.get("calorie_target"))
            days = 2 if delta <= 800 else 3
            per_day = delta / days
            allowed = max(0.0, min(per_day, target * MAX_TRIM_RATIO if target else per_day))
            new_min = max(base_min - allowed, floor) if base_min else floor
            actual = max(0.0, base_min - new_min) if base_min else allowed
            safety_notes: list[str] = []
            if actual < allowed - 1:
                safety_notes.append(f"S2：下调已到地板 {int(floor)} kcal（max(BMR, 维持×0.8)），不能再低")
            opts.append({
                "kind": "next_days_trim",
                "label": f"后面 {days} 天每天少 {int(round(actual))} kcal",
                "detail": f"分到后面 {days} 天，每天只动 {int(round(actual))} kcal——"
                          "那几天会按新区间重排（顺便换掉重复的菜），吃不出来但能拉平。",
                "reason": f"超出 {int(delta)} kcal，摊开比一天硬扛可持续",
                "recommended": not bool(remaining),
                "safe": actual > 0,
                "safety": safety_notes,
                "payload": {"days": days, "per_day": int(round(actual)),
                            "new_min": int(round(new_min)) if base_min else None},
            })

    # ---- 4. 不足：补吃 / 加量 / 换做法 ----
    if not kind_over:
        gap = delta
        if nutrient == "protein" or nutrient == "calorie":
            target_meal = remaining[0] if remaining else None
            if target_meal and spec["allow_trim"]:
                opts.append({
                    "kind": "next_meal_boost",
                    "label": f"在{ diet_intake.MEAL_LABELS.get(target_meal, target_meal)}里补上",
                    "detail": (f"加一份优质蛋白（{_supplement_suggestions(db, user_id, day, 'protein')}）"
                               if nutrient == "protein"
                               else f"把还没吃的那餐主食加半份，或加一点{_supplement_suggestions(db, user_id, day, 'calorie', 2)}"),
                    "reason": "菜还没吃，加在正餐里比单独加餐自然，也不会多出一顿",
                    "recommended": True,
                    "safe": True,
                    "safety": ["S4：补吃挂在已有餐次上"],
                    "payload": {"meal_type": target_meal, "gap": int(round(gap))},
                })
            opts.append({
                "kind": "today_supplement",
                "label": "补一点（不新增餐次）",
                "detail": (f"{_supplement_suggestions(db, user_id, day, 'protein')} 任选"
                           if nutrient == "protein"
                           else f"{_supplement_suggestions(db, user_id, day, 'calorie')} 任选"),
                "reason": "蛋白和热量都不会自己补上，缺的量要么加在餐里、要么加在餐后",
                "recommended": not bool(remaining),
                "safe": True,
                "safety": ["S4：不额外开一顿，挂在现有餐次后"],
                "payload": {"gap": int(round(gap))},
            })
        opts.append({
            "kind": "ask_reason",
            "label": "先说说怎么回事",
            "detail": "是吃不下，还是没时间做/没时间吃？——吃不下就改体积和做法（总量不动），"
                      "没时间就合并餐次（总量也照旧）。两个答案改法完全不同，不该由系统替你猜。",
            "reason": "不足的原因决定改法，不问清楚改错方向更麻烦",
            "recommended": False,
            "safe": True,
            "safety": [],
            "payload": {"question": "eat_less_or_no_time"},
        })
        # ---- 热量持续吃不完：后面几天按实际食量重排（不是逼用户补吃）----
        # 连续吃不下说明方案偏大，改方案比让人硬塞可持续；地板与单次幅度走 S2
        if nutrient == "calorie" and gap >= CARRY_MIN_KCAL:
            floor = _bmr_floor(targets)
            base_min = _num(targets.get("calorie_min"))
            target = _num(targets.get("calorie_target"))
            if base_min:
                per_day = max(50.0, min(gap / 2.0, target * MAX_TRIM_RATIO if target else gap))
                new_min = max(base_min - per_day, floor)
                if new_min < base_min - 1:
                    opts.append({
                        "kind": "next_days_loosen",
                        "label": f"后面 2 天按实际食量重排（每天少排 {int(round(base_min - new_min))} kcal）",
                        "detail": "不是让你补吃——方案排多了就改方案。后面几天按新区间重排，"
                                  "营养七项下限不动，也不低于基础代谢。",
                        "reason": "连续吃不完说明方案偏大，校准到你能执行的水平",
                        "recommended": True,
                        "safe": True,
                        "safety": ["S2：下调有地板 max(BMR, 维持×0.8)"],
                        "payload": {"days": 2, "per_day": int(round(base_min - new_min))},
                    })

    # ---- 5. 永远有「不处置」 ----
    if attribution == "extra":
        opts.insert(0, {
            "kind": "no_action",
            "label": "就这样，明天照常",
            "detail": "额外吃的是你自己选的，记下来就够了。明天按方案吃，一周的量不会被一顿带偏。",
            "reason": "S5：额外摄入是主动选择，不指责、不要求补偿",
            "recommended": True,
            "safe": True,
            "safety": [],
        })
    elif not any(o["kind"] == "no_action" for o in opts):
        opts.append({
            "kind": "no_action",
            "label": "这次不处置",
            "detail": "偶尔一天的偏离不影响一周的结果，不用补偿。想调整就选上面那些。",
            "reason": "S5：任何场景都允许不处置",
            "recommended": False,
            "safe": True,
            "safety": [],
        })

    for opt in opts:
        opt.setdefault("payload", {})
        opt["window"] = window
        opt["window_label"] = spec["label"]
    return opts


# ---------------- 落地（执行层） ----------------

def _plan_day(db: Session, user_id: int, day: date) -> PlanDay | None:
    from app.services import day_context
    return day_context.active_plan_day(db, user_id, day)


# ---------------- 重排（改目标必须连带改菜） ----------------

def _profile_ctx(db: Session, user_id: int) -> dict:
    """处置重排用的档案上下文（与生成方案同一套字段，只是不带当次请求的面板覆盖）

    没有这个就只能改数字不能改菜——那正是「后续几天菜一模一样」的根因。
    """
    from app.models.health_profile import HealthProfile

    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user_id).first()
    if not profile:
        return {}
    return {
        "height_cm": profile.height_cm, "weight_kg": profile.weight_kg,
        "bmi": nutrition.calc_bmi(profile.weight_kg, profile.height_cm),
        "age": profile.age, "gender": profile.gender,
        "activity_level": profile.activity_level or "moderate",
        "goal": profile.goal or "maintain",
        "body_fat_pct": profile.body_fat_pct,
        "daily_target_calories": nutrition.calc_daily_target(
            profile.weight_kg, profile.height_cm, profile.age, profile.gender,
            profile.activity_level, profile.goal, profile.body_fat_pct,
        ) or profile.daily_calorie_target,
        "taste_preferences": profile.taste_preferences or [],
        "dietary_restrictions": profile.dietary_restrictions or [],
        "exercise_preferences": profile.exercise_preferences or [],
        "sleep_time": profile.sleep_time, "wake_time": profile.wake_time,
        "water_target_ml": profile.water_target_ml,
    }


def _prior_plan_days(db: Session, user_id: int, before: date, limit: int = 3) -> list:
    """重排时的「前几天」：跨天不重复约束要吃真实已定的菜，否则重排出来又和昨天撞上"""
    rows = (db.query(PlanDay)
            .join(DietPlan, DietPlan.id == PlanDay.plan_id)
            .filter(DietPlan.user_id == user_id, PlanDay.date < before,
                    PlanDay.meals.isnot(None))
            .order_by(PlanDay.date.desc()).limit(limit).all())
    return list(reversed(rows))


def _recent_names(db: Session, user_id: int, before: date, limit: int = 3) -> set[str]:
    """最近几天已经吃过的菜名：换菜时避开它们，否则「换完还是那几样」"""
    names: set[str] = set()
    for row in _prior_plan_days(db, user_id, before, limit):
        for meal in (row.meals or []):
            for item in (meal.get("items") or []):
                if item.get("name"):
                    names.add(str(item["name"]))
    return names


def _diff_meals(old_meals: list | None, new_meals: list) -> list[dict]:
    """重排前后差了什么。结构统一成 {meal, from, to, why}，
    和 `_scale_meal` 的 changed 一样，前端只处理一种形状。"""
    out: list[dict] = []
    new_by = {(m.get("meal_type") or ""): m for m in (new_meals or [])}
    for old in old_meals or []:
        meal_type = old.get("meal_type") or ""
        label = diet_intake.MEAL_LABELS.get(meal_type, meal_type)
        new = new_by.get(meal_type) or {}
        old_names = [i.get("name") for i in (old.get("items") or []) if i.get("name")]
        new_names = [i.get("name") for i in (new.get("items") or []) if i.get("name")]
        for name in old_names:
            if name not in new_names:
                out.append({"meal": label, "from": name, "to": None, "why": "重排后不再排"})
        for name in new_names:
            if name not in old_names:
                out.append({"meal": label, "from": None, "to": name, "why": "重排后新增"})
    return out


def _rebuild_day(db: Session, user_id: int, day_obj: PlanDay,
                 day_targets: dict, *, reason: str = "处置下调热量区间后重排") -> dict | None:
    """按调整后的区间重排这一天；做不到就返回 None（调用方降级为缩份量）

    为什么必须重排：只把 target 数字下调、菜照旧，用户按方案吃完就会被判「超」——
    这个越界是处置自己造的，归因还会写成「方案排错」，于是又去缩方案，绕成死循环。
    """
    from app.services import planner
    from app.services.llm import foods_data

    # 已经吃过/打过卡的天不重排：换掉用户吃过的菜，记录和方案就对不上了
    eaten = (db.query(CheckIn)
             .filter(CheckIn.user_id == user_id, CheckIn.check_date == day_obj.date,
                     CheckIn.task_key.like("meal_%"), CheckIn.status == "done")
             .first())
    if eaten:
        return None
    ctx = _profile_ctx(db, user_id)
    if not ctx:
        return None
    try:
        built = planner.build_day(ctx, day_obj.day_index, 7,
                                  _prior_plan_days(db, user_id, day_obj.date),
                                  targets_override=day_targets)
    except Exception as exc:  # noqa: BLE001 —— 求解不出就降级，别把处置卡死
        logger.warning("处置重排 %s 失败：%s", day_obj.date, exc)
        return None

    old_meals = list(day_obj.meals or [])
    meals: list[dict] = []
    for meal in built.meals:
        items = []
        for item in meal.items:
            food = foods_data.find_food(item.name) or {}
            items.append({"name": item.name, "portion_g": item.portion_g,
                          "category": food.get("category") or ""})
        meals.append({"meal_type": meal.meal_type, "name": meal.name, "items": items})
    recomputed = recalc_meals(meals)

    changed = _diff_meals(old_meals, recomputed["meals"])
    day_obj.meals = recomputed["meals"]
    day_obj.nutrition = _seven_totals(recomputed["meals"])
    day_obj.edit_count = (day_obj.edit_count or 0) + 1
    day_obj.last_edited_at = datetime.now()
    flags = dict(day_obj.day_flags or {})
    flags["replanned"] = {
        "reason": reason,
        "calorie_min": day_targets.get("calorie_min"),
        "calorie_max": day_targets.get("calorie_max"),
        "changed": changed,
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    day_obj.day_flags = flags
    return {"total_after": day_obj.nutrition, "changed": changed}


# 缩份量分档：小量缩克数（察觉不到）／中量换同类菜（菜变了但不缩水）／大量两者都上
TRIM_SWAP_MIN = 150
TRIM_MAX = 650


def _kind_of(item: dict) -> str:
    """主食 / 蔬菜 / 水果 / 蛋白 / 汤 / 其他——决定「能不能换、换哪一类」

    汤单列一类：汤换凉拌菜热量虽然对得上，但这不叫替换。
    荤菜/素菜这种 category 必须显式认，否则「土豆炖牛肉」会被名字里的「土豆」
    带进主食池，一换就把蛋白也换了。
    """
    category = (item.get("category") or "").strip()
    name = item.get("name") or ""

    if "汤" in category or "汤" in name:
        return "soup"
    if "主食" in category or "谷" in category or "薯" in category:
        return "staple"
    if any(k in category for k in ("素菜", "蔬菜", "菌", "沙拉")):
        return "veg"
    if "水果" in category:
        return "fruit"
    if any(k in category for k in ("荤菜", "肉", "蛋", "豆", "水产", "蛋白", "奶")):
        return "protein"
    if any(k in category for k in ("小吃", "饮品", "饮料", "零食")):
        return "other"

    if any(k in name for k in ("米饭", "饭", "面", "粥", "馒头", "饼", "薯", "燕麦", "玉米",
                               "藜麦", "土豆", "山药", "芋", "南瓜")):
        return "staple"
    if any(k in name for k in ("菜", "瓜", "菌", "菇", "萝卜", "番茄", "笋", "木耳", "海带",
                               "花", "椒", "茄", "芹", "葱", "蒜", "苗", "豆角", "秋葵", "蔬")):
        return "veg"
    if any(k in name for k in ("苹果", "香蕉", "橙", "莓", "梨", "桃", "葡萄")):
        return "fruit"
    if any(k in name for k in ("鸡", "鱼", "虾", "蛋", "豆腐", "牛", "猪", "羊")):
        return "protein"
    return "other"


def _form_of(name: str) -> str:
    """主食的形态：饭 / 面 / 粥 / 馍 / 薯。

    换主食必须同形态——「米饭换成牛肉拉面」热量虽然对得上，
    但汤、油、钠全变了，不是「等热量替换」，是换了一顿饭。
    """
    name = name or ""
    if any(k in name for k in ("粥", "糊", "羹")):
        return "porridge"
    if any(k in name for k in ("面", "粉", "米线", "河粉", "馄饨", "饺")):
        return "noodle"
    if any(k in name for k in ("馒头", "包子", "饼", "花卷", "馍")):
        return "bun"
    if any(k in name for k in ("饭", "米")):
        return "rice"
    if any(k in name for k in ("薯", "山药", "芋", "玉米", "南瓜", "土豆")):
        return "root"
    return "other"


def _swap_item(item: dict, meal_type: str, kind: str, avoid: set[str]) -> dict | None:
    """给一道菜找「每 100 g 热量接近」的同类替代，克数按等热量换算

    只换主食/配菜，不换蛋白主菜：蛋白主菜一换，蛋白区间就可能越界。
    主食还要求同形态（饭换饭、面换面）。
    优先挑没在最近几天出现过的，这正是「调完还是那几样」的解药。
    """
    from app.services.llm import foods_data

    base = foods_data.find_food(item.get("name") or "") or {}
    base_kcal = _num((base.get("per_100g") or {}).get("calories"))
    portion = _num(item.get("portion_g"))
    if not base_kcal or not portion:
        return None
    base_form = _form_of(item.get("name") or "")

    best = None
    for food in foods_data.load_foods():
        name = food.get("name") or ""
        if not name or name == item.get("name") or name in avoid:
            continue
        if meal_type and meal_type not in (food.get("suitable_meals") or []):
            continue
        if _kind_of({"category": food.get("category") or "", "name": name}) != kind:
            continue
        if kind == "staple" and base_form != "other" and _form_of(name) != base_form:
            continue
        kcal = _num((food.get("per_100g") or {}).get("calories"))
        if not kcal:
            continue
        ratio = abs(kcal - base_kcal) / base_kcal
        if ratio > 0.35:      # 差得太多就不是「替换」，是换了一顿饭
            continue
        if best is None or ratio < best["ratio"]:
            best = {"name": name, "category": food.get("category") or item.get("category") or "",
                    "kcal": kcal, "ratio": ratio}
    if not best:
        return None

    new_portion = portion * base_kcal / best["kcal"]
    return {"name": best["name"], "category": best["category"],
            "portion_g": max(20.0, round(new_portion / 5.0) * 5.0)}


def _seven_totals(meals: list[dict]) -> dict:
    """七项合计（PlanDay.nutrition 是七项口径，只更四项会和详情页对不上）

    必须含全日烹调油（+225 kcal/+25 g 脂肪）：求解器排的方案天（planner
    _day_nutrients）和 plans._meals_nutrition 都含油，唯独这里漏了——
    重排后的天会比求解器实际卡的区间低 225 kcal，复核判定和展示全错位。
    """
    from app.services.planner import OIL_NUTRIENTS

    keys = ("calories", "protein_g", "fat_g", "carb_g", "fiber_g", "sodium_mg", "sugar_g")
    total = {key: 0.0 for key in keys}
    for meal in meals:
        for item in meal.get("items") or []:
            values = nutrition.match_diet_nutrition(item.get("name") or "",
                                                   _num(item.get("portion_g")),
                                                   item.get("category") or "")
            for key in keys:
                total[key] += _num(values.get(key))
    for key, value in OIL_NUTRIENTS.items():
        total[key] = total.get(key, 0.0) + value
    return {key: round(value, 1) for key, value in total.items()}


def _scale_meal(day_obj: PlanDay, meal_types: list[str], trim_kcal: float,
                targets: dict, *, recent_names: set[str] | None = None,
                allow_swap: bool = True) -> dict:
    """让这几餐合计热量下降 trim_kcal：小量缩克数、中量换菜、大量两者都上

    为什么要换菜而不是一律缩：连调几天就变成「同样的菜、不同的克数」，
    用户会觉得系统只会让他少吃；而且主食缩到 60% 时实际减掉的量远超他点的数。

    allow_swap=False 用于已经吃过的天：那时换菜会和用户的饮食记录对不上，只能缩份量。
    """
    recent = {str(n) for n in (recent_names or []) if n}
    meals = recalc_meals(day_obj.meals or [])["meals"]
    picked = [m for m in meals if (m.get("meal_type") or "").strip().lower() in meal_types]
    # item_nutrition 是**平铺**结构（calories 在顶层，没有 nutrition 子字典），
    # 之前读 item["nutrition"]["calories"] 恒得 0，于是每次都按 60% 硬砍——已修
    current = sum(_num(i.get("calories")) for m in picked for i in (m.get("items") or []))

    changed: list[dict] = []
    avoided: set[str] = set(recent)

    # 1) 中量以上先换菜：每餐换 1 道，最多 3 道
    if allow_swap and trim_kcal > TRIM_SWAP_MIN:
        swapped_meals: list[dict] = []
        for meal in meals:
            meal_type = (meal.get("meal_type") or "").strip().lower()
            if meal_type not in meal_types:
                swapped_meals.append(meal)
                continue
            items: list[dict] = []
            done_here = False
            for item in (meal.get("items") or []):
                if (not done_here and len(avoided) - len(recent) < 3
                        and _kind_of(item) in ("staple", "veg")):
                    replaced = _swap_item(item, meal_type, _kind_of(item), avoided)
                    if replaced:
                        changed.append({
                            "meal": diet_intake.MEAL_LABELS.get(meal_type, meal_type),
                            "from": item.get("name"), "to": replaced["name"],
                            "why": "换同类菜，热量相当",
                        })
                        avoided.add(replaced["name"])
                        done_here = True
                        items.append({**item, **replaced})
                        continue
                items.append(item)
            swapped_meals.append({**meal, "items": items})
        if changed:
            meals = recalc_meals(swapped_meals)["meals"]

    # 2) 换完还不够 (或量本来就小) → 缩主食，蛋白和配菜不动
    after = _num(recalc_meals(meals)["total"].get("calories"))
    need = trim_kcal - max(0.0, current - after)
    factor = 1.0
    if need > 30:
        # 只能缩到 60%：再低就不是"少排一点"，而是把一顿饭砍成半顿，用户会直接不吃
        factor = max(0.6, 1 - need / max(after, 1))
        shrunk: list[dict] = []
        shrunk_items = 0
        for meal in meals:
            meal_type = (meal.get("meal_type") or "").strip().lower()
            if meal_type not in meal_types:
                shrunk.append(meal)
                continue
            items = []
            for item in (meal.get("items") or []):
                portion = _num(item.get("portion_g"))
                new_portion = portion
                if _kind_of(item) == "staple":
                    new_portion = max(20.0, round(portion * factor / 5.0) * 5.0)
                    shrunk_items += 1
                    if new_portion < portion - 1:
                        changed.append({
                            "meal": diet_intake.MEAL_LABELS.get(meal_type, meal_type),
                            "from": f"{item.get('name')} {portion:g}g",
                            "to": f"{item.get('name')} {new_portion:g}g",
                            "why": "份量下调",
                        })
                items.append({**item, "portion_g": new_portion})
            shrunk.append({**meal, "items": items})
        if shrunk_items:
            meals = shrunk
        else:
            # 这餐没有主食可减 → 整体缩（蛋白类保留）
            meals = apply_portion_rebalance(meals, factor)

    day_obj.meals = meals
    day_obj.edit_count = (day_obj.edit_count or 0) + 1
    day_obj.last_edited_at = datetime.now()
    day_obj.nutrition = _seven_totals(meals)
    flags = dict(day_obj.day_flags or {})
    flags["adjusted"] = {
        "reason": "按用户选择的处置下调这天",
        "meals": meal_types,
        "trim_kcal": int(round(trim_kcal)),
        "changed": changed,
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    day_obj.day_flags = flags
    return {"meals": meal_types, "trim_kcal": int(round(trim_kcal)),
            "portion_factor": round(factor, 3),
            "changed": changed,
            "total_after": day_obj.nutrition}


def _trim_next_days(db: Session, user_id: int, from_day: date, days: int,
                    per_day: float, targets: dict, *,
                    reason_label: str = "超出") -> list[dict]:
    """把后续几天的目标区间下移并**连带重排那天**，并留痕

    只改数字不重排会造出两种错误：那天被编辑过（edit_count>0）时目标生效但菜还是原量，
    用户按方案吃完就判超；没编辑过时 `targets_for_day` 根本不采纳当日覆盖，白改。
    所以两件事一起做：写目标 → edit_count+1 → 按新区间重排。
    已吃过的天不重排，只缩份量。
    """
    floor = _bmr_floor(targets)
    touched: list[dict] = []
    for offset in range(1, days + 1):
        target_day = from_day + timedelta(days=offset)
        day_obj = _plan_day(db, user_id, target_day)
        if not day_obj:
            continue
        t = dict(day_obj.targets or {})
        base_min = _num(t.get("calorie_min")) or _num(targets.get("calorie_min"))
        base_max = _num(t.get("calorie_max")) or _num(targets.get("calorie_max"))
        if not base_min:
            continue
        new_min = max(base_min - per_day, floor)
        new_max = max(base_max - per_day, new_min + 100)
        if new_min >= base_min - 1:
            continue
        t["calorie_min"] = int(round(new_min))
        t["calorie_max"] = int(round(new_max))
        t["adjusted_from"] = {"calorie_min": int(round(base_min)),
                              "calorie_max": int(round(base_max))}
        note = (f"这天因 {from_day} {reason_label} {int(per_day)} kcal/天 的处置，区间下调到 "
                f"{int(round(new_min))}~{int(round(new_max))} kcal（地板 {int(floor)}）")

        # 先落目标再重排：求解器要按新区间解
        day_obj.targets = t
        rebuilt = _rebuild_day(db, user_id, day_obj, t, reason=note)
        if rebuilt:
            changed = rebuilt.get("changed") or []
            swaps = len([c for c in changed if c.get("to")])
            t["calorie_band_note"] = note + (
                f"；已按新区间重排（换了 {swaps} 道菜）" if swaps else "；已按新区间重排")
        else:
            # 换不了菜（菜池太窄 / 这天已经吃过）→ 缩份量兜底，并说清楚为什么没换
            existing_meals = [(m.get("meal_type") or "") for m in (day_obj.meals or [])]
            info = _scale_meal(day_obj, [m for m in existing_meals if m],
                               per_day, targets,
                               recent_names=_recent_names(db, user_id, target_day),
                               allow_swap=False)   # 这天重排不了，多半是已经吃过，别再换菜
            t["calorie_band_note"] = note + (
                f"；没换菜，只能把份量缩到 {int((info.get('total_after') or {}).get('calories') or 0)} kcal")
        day_obj.targets = t
        touched.append({"date": str(target_day), "calorie_min": t["calorie_min"],
                        "calorie_max": t["calorie_max"],
                        "replanned": bool(rebuilt),
                        "changed": (rebuilt or {}).get("changed") or []})
    return touched


def apply(db: Session, user_id: int, deviation: DietDeviation, kind: str,
          payload: dict | None = None, *, targets: dict, now: datetime | None = None) -> dict:
    """执行一条处置。返回做了什么的明细（前端直接用这段文案做确认提示）。"""
    now = now or datetime.now()
    payload = payload or {}
    day = deviation.dev_date
    day_obj = _plan_day(db, user_id, day)
    result: dict = {"kind": kind, "detail": [], "touched": []}
    adjustment = PlanAdjustment(
        user_id=user_id, deviation_id=deviation.id, kind=kind,
        effective_date=day, status="applied", applied_at=now,
        payload=payload, reason=None,
    )

    if kind == "trim_remaining":
        meal_types = payload.get("meals") or []
        trim = _num(payload.get("trim_kcal"))
        if day_obj and meal_types and trim > 0:
            info = _scale_meal(day_obj, meal_types, trim, targets,
                               recent_names=_recent_names(db, user_id, day))
            adjustment.scope = "today"
            swaps = [c for c in (info.get("changed") or []) if c.get("why") == "换同类菜，热量相当"]
            result["detail"].append(
                f"已把{'、'.join(diet_intake.MEAL_LABELS.get(m, m) for m in meal_types)}"
                f"调到 {int(info['total_after'].get('calories') or 0)} kcal"
                + (f"（{'、'.join(c['from'] + '换成' + c['to'] for c in swaps)}）" if swaps else ""))
            result["touched"].append({"plan_day_id": day_obj.id, **info})
            guard = _refresh_check_ins(db, user_id, day, day_obj)
            if guard:
                result["detail"].append(guard)

    elif kind == "today_exercise":
        minutes = int(_num(payload.get("minutes")))
        if day_obj and minutes > 0:
            exercises = [dict(x) for x in (day_obj.exercises or ([day_obj.exercise] if day_obj.exercise else []))]
            if exercises:
                main = exercises[0]
                per_min = _bump_per_min(targets)
                main["duration_min"] = int(_num(main.get("duration_min"))) + minutes
                main["calories_burned"] = int(round(per_min * main["duration_min"]))
                main["tips"] = (main.get("tips") or "") + \
                    f"；今天餐食超限，追加 {minutes} 分钟（只抵一半，另一半明天照常）"
                exercises[0] = main
                day_obj.exercises = exercises
                day_obj.exercise = main
                day_obj.edit_count = (day_obj.edit_count or 0) + 1
                adjustment.scope = "today"
                result["detail"].append(f"今天的{main.get('type') or '运动'}已加到 {main['duration_min']} 分钟")
                result["touched"].append({"plan_day_id": day_obj.id, "exercise": main})
                _refresh_check_ins(db, user_id, day, day_obj)
            else:
                result["detail"].append("今天方案里没有运动任务，只能靠缩餐或明天照常")
                adjustment.status = "expired"

    elif kind == "next_days_trim":
        days = int(_num(payload.get("days")) or 2)
        per_day = _num(payload.get("per_day"))
        touched = _trim_next_days(db, user_id, day, days, per_day, targets)
        adjustment.scope = "next_days"
        if touched:
            replanned = [t_ for t_ in touched if t_.get("replanned")]
            swaps = sum(len([c for c in (t_.get("changed") or []) if c.get("to")])
                        for t_ in touched)
            result["detail"].append(
                f"后面 {len(touched)} 天的热量区间已下调（每天 {int(per_day)} kcal，不低于基础代谢）"
                + (f"，其中 {len(replanned)} 天按新区间重排、换了 {swaps} 道菜"
                   if replanned else "，剩余的天只能缩份量（菜不够换）"))
        else:
            result["detail"].append(
                "后面几天没有可调的空间：摄入已经在下限（max(基础代谢, 维持×0.8)），"
                "再压不安全。这次只能加练或明天照常")
            adjustment.status = "expired"
        result["touched"] = touched

    elif kind == "next_days_loosen":
        # 吃少了的跨天处置：方案偏大就改方案，不逼用户补吃。
        # 机制与 next_days_trim 完全同一条路（区间下调 + 连带重排），
        # 只是理由文案不同——理由必须诚实，用户得知道为什么后面变少了。
        days = int(_num(payload.get("days")) or 2)
        per_day = _num(payload.get("per_day"))
        touched = _trim_next_days(db, user_id, day, days, per_day, targets,
                                  reason_label="按实际食量校准")
        adjustment.scope = "next_days"
        if touched:
            replanned = [t_ for t_ in touched if t_.get("replanned")]
            swaps = sum(len([c for c in (t_.get("changed") or []) if c.get("to")])
                        for t_ in touched)
            result["detail"].append(
                f"后面 {len(touched)} 天已按你能吃下的量重排"
                f"（每天少排 {int(per_day)} kcal，不低于基础代谢，营养下限不动）"
                + (f"，共换了 {swaps} 道菜" if replanned else ""))
        else:
            result["detail"].append(
                "后面几天没有可下调的空间（已经在代谢地板附近），保持原方案")
            adjustment.status = "expired"
        result["touched"] = touched

    elif kind == "next_meal_boost":
        adjustment.scope = "today"
        meal_type = payload.get("meal_type")
        result["detail"].append(
            f"把{diet_intake.MEAL_LABELS.get(meal_type, meal_type or '下一餐')}加一份，"
            "吃的时候顺手拍照记一下，指标会跟着更新")
        adjustment.status = "proposed"   # 需要用户真去吃，不是系统能代劳的

    elif kind == "today_supplement":
        adjustment.scope = "today"
        adjustment.status = "proposed"
        result["detail"].append("补吃的东西吃到后记一笔，系统会把它算进当天摄入（不新增餐次）")

    elif kind == "ask_reason":
        adjustment.scope = "today"
        adjustment.status = "proposed"
        result["detail"].append("等用户回答「吃不下 / 没时间」再决定改法")

    elif kind == "portion_advice":
        # 纯提示类：用户下次盛饭就按克数来，系统这边没有可代劳的动作。
        # 记 applied 而不是 proposed——它不是"等你去做"的任务，追着问反而烦
        adjustment.scope = "today"
        result["detail"].append(
            "已记下：这餐是份量偏大，下次照方案克数盛就正好（方案不用改，也不用少吃菜）")

    elif kind == "no_action":
        adjustment.scope = "today"
        result["detail"].append("已记下：这次不处置，明天照常")

    elif kind == "fix_plan":
        adjustment.scope = "today"
        adjustment.status = "proposed"
        result["detail"].append("已记入方案修复队列")

    else:
        adjustment.status = "expired"
        result["detail"].append("不认识的处置方式，已忽略")

    if adjustment.reason is None:
        adjustment.reason = "；".join(result["detail"]) or None
    deviation.status = "ignored" if kind == "no_action" else "handled"
    deviation.handled_at = now
    db.add(adjustment)
    db.flush()
    result["adjustment_id"] = adjustment.id
    result["deviation_status"] = deviation.status
    return result


def _refresh_check_ins(db: Session, user_id: int, day: date, day_obj: PlanDay) -> str | None:
    """改了方案天之后，把还没打卡的餐次任务保持可用（任务本身只存 key，不用改文案）"""
    pending = (db.query(CheckIn)
               .filter(CheckIn.user_id == user_id, CheckIn.check_date == day,
                       CheckIn.status == "pending", CheckIn.task_key.like("meal_%"))
               .count())
    if not pending:
        return "今天的餐都打卡了，改动只影响方案展示（记录已按实际吃的算）"
    return None
