"""今日体检清单：把「今天哪几项没落在区间里」一次说清，并给出
① 现在还能做什么（now） ② 系统后续会怎么调（later）

为什么单独一层：
- 旧版只有进度条各自染色，用户得自己一项项找；纤维/钠/添加糖/食物种类
  压根没判；更要命的是「没达标之后会怎样」从后端到前端都没有一条数据。
- 这层**只读、不改库**。判定口径全部复用 metric_bands / nutrition_check，
  绝不另起一套阈值，否则会出现「卡片说达标、清单说没达标」的自相矛盾。

九项（不含维生素与矿物质）：
热量 / 蛋白 / 纤维 / 钠 / 添加糖 / 饮水 / 睡眠 / 运动 / 食物种类

为什么不做维生素：
食物库 extra_nutrients 字段有记录的条目内容全是空对象，没有任何可信数据。
硬编一个「维生素达标」就是假指标，比缺失更糟——宁可这块空着，也不骗用户。

严重度（决定排序与颜色，不决定是否上报）：
- high   热量 / 蛋白 / 饮水 / 睡眠——数据可信、直接影响方案，必须让用户看见
- medium 运动 / 纤维——行为项与结构项，提醒为主
- low    钠 / 添加糖 / 食物种类——菜库对这三项的覆盖不全（烹调盐未计入），只做提示
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.check_in import CheckIn
from app.models.food_record import FoodRecord
from app.services import food_layers, metric_bands, nutrition_check
from app.services.meal_recalc import MEAL_LABELS

SPECIES_MIN = food_layers.SPECIES_PER_DAY_MIN   # 12 种/天（膳食指南口径）

SEVERITY = {
    "calorie": "high", "protein": "high", "water": "high", "sleep": "high",
    "exercise": "medium", "fiber": "medium",
    "sodium": "low", "sugar": "low", "species": "low",
}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# 体检项 → adapt 调整理由里的字段名，用来把「后续会怎么调」贴到对应项上
FIELD_OF = {
    "calorie": "diet", "protein": "diet", "fiber": "diet",
    "sodium": "diet", "sugar": "diet", "species": "diet",
    "water": "water", "sleep": "sleep", "exercise": "exercise",
}

_LABELS = {
    "calorie": "热量", "protein": "蛋白质", "fiber": "膳食纤维",
    "sodium": "钠", "sugar": "添加糖", "water": "饮水",
    "sleep": "睡眠", "exercise": "运动消耗", "species": "食物种类",
}

# 「现在还能做什么」——只给今天真的做得到的动作，不给口号
# 一句话原则：手机上两行以内说完。卡片上每项只占「差多少 / 现在做什么」两行，
# 后续调整由 adapt 的预告统一讲，不在这里重复一遍。
_NOW = {
    "calorie_over": "只喝水/无糖茶，别再加餐；明天照新方案吃",
    "calorie_under": "还有餐就吃完；吃完了别硬塞，明天正常吃",
    "protein_under": "下一餐补蛋白：鸡胸 100 g / 鸡蛋 2 个 / 豆腐 200 g",
    "protein_over": "别再叠蛋白粉或大块肉，蔬菜照吃",
    "fiber_under": "加一份蔬菜（约 300 g），或主食换糙米/燕麦",
    "sodium_over": "别再碰腌制、加工肉和重口味外卖",
    "sugar_over": "停掉含糖饮料和甜点，用水果替代",
    "water_under": "补 300~500 ml，睡前 1 小时别猛灌",
    "sleep_under": "今晚按点睡，睡前 1 小时不看手机",
    "exercise_under": "有空快走 20 分钟；没空明天照做，不用补练",
    "exercise_over": "今天够了，明天按恢复强度做",
    "species_under": "明天加一样今天没吃的：深色蔬菜/水果/杂粮",
}


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _item(key: str, *, actual, low, high, unit: str, status: str,
          gap_text: str, now: str, later: str = "") -> dict:
    return {
        "key": key,
        "label": _LABELS[key],
        "unit": unit,
        "actual": actual,
        "min": low,
        "max": high,
        "status": status,
        "severity": SEVERITY.get(key, "medium"),
        "gap_text": gap_text,
        "now": now,
        "later": later,
        "source": FIELD_OF.get(key, "diet"),
    }


def _status(value: float | None, low: float | None, high: float | None) -> str:
    if value is None:
        return "unknown"
    if low is not None and value < low:
        return "under"
    if high is not None and value > high:
        return "over"
    return "in_range"


def _species_names(db: Session, user_id: int, day: date, plan_day,
                   done_meal_types: set[str]) -> set[str]:
    """当天真正吃到的食材种类（去烹调形态后的 canonical 名）

    两路合并：有食物记录的按记录名；打卡了但没记的餐次按方案菜名
    （用户点了「吃了」但没记，实际吃的就是方案那几道）。
    """
    names: set[str] = set()
    recorded_meals: set[str] = set()
    for row in (db.query(FoodRecord)
                .filter(FoodRecord.user_id == user_id, FoodRecord.eaten_date == day,
                        FoodRecord.voided_at.is_(None)).all()):
        name = food_layers.canonical_name(row.dish_name or "")
        if name:
            names.add(name)
        meal = (row.meal_type or "").strip().lower()
        if meal:
            recorded_meals.add(meal)

    for meal in (plan_day.meals or []) if plan_day else []:
        meal_type = (meal.get("meal_type") or "").strip().lower()
        if meal_type not in done_meal_types or meal_type in recorded_meals:
            continue
        for item in meal.get("items") or []:
            name = food_layers.canonical_name(item.get("name") or "")
            if name:
                names.add(name)
    return names


def audit(db: Session, user_id: int, day: date, ctx: dict,
          next_plan: dict | None = None) -> dict:
    """产出今日体检清单。

    ctx 由 day_summary 传进来（那边已经算过一轮，别重复查库）：
      targets / band_targets / intake / burn / water_ml / sleep_h / tasks / plan_day
    next_plan 是 adapt.preview 的结果，用来把「后续会怎么调」贴到对应项上。
    """
    targets = ctx.get("targets") or {}
    band_targets = ctx.get("band_targets") or targets
    intake = ctx.get("intake") or {}
    burn = ctx.get("burn") or {}
    water_ml = ctx.get("water_ml")
    sleep_h = ctx.get("sleep_h")
    tasks = ctx.get("tasks") or []
    plan_day = ctx.get("plan_day")

    pending_meals = [c for c in tasks if c.status == "pending"
                     and (c.task_key or "").startswith("meal_")]
    pending_labels = [MEAL_LABELS.get((c.task_key or "").replace("meal_", ""), "")
                      for c in pending_meals]
    has_pending = bool(pending_meals)

    # 结算状态：餐次没打卡就是「还没吃」（全站口径一致），但运动没打卡要看时点——
    # 下午五点还没练很正常，过了结算时点还空着才是「没做到」。
    settled = ctx.get("settled")
    if settled is None:
        try:
            from app.services import settlement
            settled = bool(settlement.settlement_of(day).get("settled"))
        except Exception:  # noqa: BLE001
            settled = True

    items: list[dict] = []

    # ---- 1~5：饮食五项（复用 nutrition_check 的严格区间，与方案自检同口径）----
    diet_source = {
        "calorie": intake.get("calories"),
        "protein": intake.get("protein_g"),
        "fiber": intake.get("fiber_g"),
        "sodium": intake.get("sodium_mg"),
        "sugar": intake.get("sugar_g"),
    }
    for key in ("calorie", "protein", "fiber", "sodium", "sugar"):
        raw = diet_source.get(key)
        if key in ("calorie", "protein") and not raw:
            continue                      # 没记录时热量蛋白由 diet_summary 判「待记录」，不重复报
        detail = nutrition_check.item_for(key, {"calories": raw} if key == "calorie"
                                          else {nutrition_check.DIET_SPECS[key][4]: raw},
                                          band_targets if key in ("calorie", "protein") else targets)
        status = detail["status"]
        if status == "unknown":
            continue
        if key in ("calorie", "protein") and has_pending and status == "under":
            status = "in_progress"        # 还有餐没打卡，不算「没达标」
        actual = detail["actual"]
        low = _num(detail["min"]) if detail["min"] is not None else None
        high = _num(detail["max"]) if detail["max"] is not None else None
        gap = ""
        if status == "under" and low is not None:
            gap = f"比下限少 {low - (actual or 0):g} {detail['unit']}"
        elif status == "over" and high is not None:
            gap = f"超上限 {(actual or 0) - high:g} {detail['unit']}"
        elif status == "in_progress":
            gap = "还有餐没打卡，吃完再判"
        items.append(_item(
            key, actual=actual, low=low, high=high, unit=detail["unit"], status=status,
            gap_text=gap,
            now=(_NOW.get(f"{key}_{status}") or _NOW.get(f"{key}_under")
                 or _NOW.get(f"{key}_over") or ""),
        ))

    # ---- 6：饮水 ----
    if water_ml is not None:
        band = metric_bands.build_band(band_targets, "water", water_ml)
        if band and band["status"] != "no_data":
            status = band["status"]
            low, high = _num(band.get("min")) or None, _num(band.get("max")) or None
            items.append(_item(
                "water", actual=band["actual"], low=low, high=high,
                unit="ml", status=status,
                gap_text=("还差 %d ml" % int(low - water_ml)) if status == "under" else
                         ("超 %d ml" % int(water_ml - high) if status == "over" else ""),
                now=_NOW.get(f"water_{status}") or "",
            ))

    # ---- 7：睡眠（昨天的睡，今天补不回来，只提示今晚）----
    if sleep_h is not None:
        band = metric_bands.build_band(band_targets, "sleep", sleep_h)
        if band and band["status"] != "no_data":
            low = _num(band.get("min")) or None
            high = _num(band.get("max")) or None
            items.append(_item(
                "sleep", actual=band["actual"], low=low, high=high, unit="h",
                status=band["status"],
                gap_text=("少 %.1f h" % (low - sleep_h)) if band["status"] == "under" else "",
                now=_NOW.get("sleep_under") or "",
            ))

    # ---- 8：运动（消耗 + 漏练）----
    burn_min = _num(targets.get("exercise_burn_min")) or None
    burn_max = _num(targets.get("exercise_burn_max")) or None
    burned = _num(burn.get("exercise_kcal"))
    exercise_tasks = [c for c in tasks if (c.check_type or "") == "exercise"]
    missed = len([c for c in exercise_tasks if c.status != "done"])
    if exercise_tasks:
        done_ex = [c for c in exercise_tasks if c.status == "done"]
        pending_ex = [c for c in exercise_tasks if c.status == "pending"]
        if not done_ex and pending_ex and not settled:
            status = "in_progress"        # 还没到练的时候，不算没做到
        else:
            status = _status(burned, burn_min, burn_max)
        gap = ""
        if status == "under":
            gap = (f"还差 {int(burn_min - burned)} kcal" if burn_min
                   else "今天还没有有效运动")
            if missed:
                gap += f"（{missed} 项没打卡）"
        elif status == "over":
            gap = f"超 {int(burned - burn_max)} kcal" if burn_max else ""
        items.append(_item(
            "exercise", actual=round(burned), low=burn_min, high=burn_max,
            unit="kcal", status=status, gap_text=gap,
            now=_NOW.get(f"exercise_{status}") or _NOW.get("exercise_under") or "",
        ))

    # ---- 9：食物种类 ----
    done_meal_types = {(c.task_key or "").replace("meal_", "")
                       for c in tasks
                       if c.status == "done" and (c.task_key or "").startswith("meal_")}
    species = _species_names(db, user_id, day, plan_day, done_meal_types)
    if species and not has_pending:
        count = len(species)
        items.append(_item(
            "species", actual=count, low=SPECIES_MIN, high=None, unit="种",
            status=("under" if count < SPECIES_MIN else "in_range"),
            gap_text=(f"还差 {SPECIES_MIN - count} 种" if count < SPECIES_MIN else ""),
            now=(_NOW["species_under"] if count < SPECIES_MIN else ""),
        ))

    # ---- 贴上「后续会怎么调」----
    actions = (next_plan or {}).get("actions") or []
    for item in items:
        field = item["source"]
        hit = [a for a in actions if a.get("field") == field]
        if hit:
            # 多个天会给出同一条动作（"明后两天各下调 80 kcal"），去重后再拼接，
            # 否则同一句话在卡片里出现两遍，看着像系统抽风
            parts: list[str] = []
            for action in hit:
                if action["action"] not in parts:
                    parts.append(action["action"])
                if len(parts) == 2:
                    break
            item["later"] = "；".join(parts)
        elif item["status"] in ("under", "over"):
            item["later"] = "这一项后续方案不动，按原计划走"
        else:
            item["later"] = ""

    failed = [i for i in items if i["status"] in ("under", "over")]
    warnings = [i for i in items if i["status"] in ("low", "high")]
    pending = [i for i in items if i["status"] == "in_progress"]
    passed = [i for i in items if i["status"] == "in_range"]

    failed.sort(key=lambda i: (SEVERITY_ORDER.get(i["severity"], 1), i["key"]))
    warnings.sort(key=lambda i: (SEVERITY_ORDER.get(i["severity"], 1), i["key"]))

    headline = _headline(failed, warnings, passed, has_pending, pending_labels)
    return {
        "date": str(day),
        "ok": not failed,
        "checked": len(items),
        "items": items,
        "failed": failed,
        "warnings": warnings,
        "pending": pending,
        "passed_keys": [i["key"] for i in passed],
        "headline": headline,
        "later_headline": ((next_plan or {}).get("headline") or ""),
    }


def _headline(failed: list[dict], warnings: list[dict], passed: list[dict],
              has_pending: bool, pending_labels: list[str]) -> str:
    """一行结论：先说结论，再说最要紧的两项"""
    if failed:
        names = "、".join(i["label"] for i in failed[:3])
        more = "…" if len(failed) > 3 else ""
        return f"{len(failed)} 项没达标：{names}{more}"
    if has_pending:
        return metric_bands.pending_meal_hint(pending_labels)
    if warnings:
        names = "、".join(i["label"] for i in warnings[:2])
        return f"{len(passed)} 项达标，{names}略偏，问题不大"
    if passed:
        return f"{len(passed)} 项全部达标"
    return "今天还没有可判定的记录"
