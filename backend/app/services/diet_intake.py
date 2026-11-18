"""当日摄入口径：实际记录优先，已打卡但没记的餐次按方案菜品折算

为什么需要这一层：
用户点「早餐已完成」= 我按这份方案吃了。此时再要求他手动记一遍菜名，
就会出现"打完卡了指标还全是待记录"的割裂感（也正是本模块要解决的问题）。

口径：
1. 用户手动/拍照记了食物 → 以实际记录为准（他知道自己真实吃了多少）
2. 没记、但这餐已打卡，且方案里这餐有菜 → 按方案的菜品营养折算
3. 两者按「餐次」互斥，绝不重复计数
4. 当天存在 meal_type 为空的历史记录时，无法判断哪餐记过了，
   此时不补方案营养（宁可少算，也不重复算）
5. **按归属拆成两块**：`plan`（方案内）与 `extra`（方案外加餐）。
   方案内越界说明方案排错了，方案外越界是用户自己的选择——不拆开就永远归因不清

返回里带 estimated / estimated_meals，前端可标注「按方案估算」，
避免把估算值当成用户真实摄入（诚实边界，别让人以为系统在监控他吃没吃）。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.check_in import CheckIn
from app.models.diet_plan import PlanDay
from app.models.food_record import FoodRecord
from app.services import day_context, nutrition
from app.services.meal_recalc import MEAL_LABELS, recalc_meals

# 餐次 -> 打卡任务 key
MEAL_TASK = {
    "breakfast": "meal_breakfast",
    "lunch": "meal_lunch",
    "dinner": "meal_dinner",
    "snack": "meal_snack",
}

_NUTRIENTS = ("calories", "protein_g", "fat_g", "carb_g", "fiber_g", "sodium_mg", "sugar_g")


def _empty() -> dict:
    return {key: 0.0 for key in _NUTRIENTS}


def _add(acc: dict, item: dict) -> None:
    for key in _NUTRIENTS:
        acc[key] += float(item.get(key) or 0)


def _record_values(record: FoodRecord) -> dict:
    """一条饮食记录的七项营养（老记录没有纤维/钠/糖，按 0 计）"""
    return {
        "calories": record.calories,
        "protein_g": record.protein_g,
        "fat_g": record.fat_g,
        "carb_g": record.carb_g,
        "fiber_g": getattr(record, "fiber_g", None),
        "sodium_mg": getattr(record, "sodium_mg", None),
        "sugar_g": getattr(record, "sugar_g", None),
    }


def plan_meal_nutrition(plan_day) -> dict[str, dict]:
    """方案各餐的七项营养（与「改菜预览」同一套匹配口径）"""
    if not plan_day or not plan_day.meals:
        return {}
    result: dict[str, dict] = {}
    for meal in recalc_meals(plan_day.meals)["meals"]:
        meal_type = (meal.get("meal_type") or "snack").strip().lower()
        acc = _empty()
        for item in meal.get("items") or []:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            values = nutrition.match_diet_nutrition(
                name, float(item.get("portion_g") or 0), item.get("category"),
            )
            _add(acc, values)
        # 同一餐次出现多次（例如两份加餐）时累加，不覆盖
        if meal_type in result:
            _add(result[meal_type], acc)
        else:
            result[meal_type] = {key: round(value, 1) for key, value in acc.items()}
    return result


def done_meal_tasks(db: Session, user_id: int, day: date) -> dict[str, list[int]]:
    """已打卡的餐次 -> 对应的 plan_day_id（新的在前）

    打卡记录自带 plan_day_id，这就是「这餐是按哪份方案吃的」的原始凭据。
    比按日期反查进行中方案稳：方案重新生成过、旧方案被归档时，日期查不到，凭据还在。
    """
    rows = (
        db.query(CheckIn.task_key, CheckIn.plan_day_id, CheckIn.id)
        .filter(
            CheckIn.user_id == user_id,
            CheckIn.check_date == day,
            CheckIn.status == "done",
        )
        .order_by(CheckIn.id.desc())
        .all()
    )
    result: dict[str, list[int]] = {}
    for task_key, plan_day_id, _ in rows:
        if not (task_key or "").startswith("meal_") or not plan_day_id:
            continue
        meal_type = task_key.replace("meal_", "")
        ids = result.setdefault(meal_type, [])
        if plan_day_id not in ids:
            ids.append(plan_day_id)
    return result


def day_intake(db: Session, user_id: int, day: date) -> dict:
    """某天实际摄入 = 食物记录 + 已打卡未记录餐次的方案营养，并按归属拆分

    同时给出 `plan_baseline`：**有记录的那些餐次**对应的方案排定营养。
    它存在的唯一目的是把两者相减得到份量偏离——
    方案内实际 950 > 基线 760，差的那 190 是「盛多了」，不是「方案排多了」。
    没有这个数，份量吃多会被归因成方案错，处置方向整个反过来。
    """
    records = (db.query(FoodRecord)
               .filter(FoodRecord.user_id == user_id, FoodRecord.eaten_date == day,
                       FoodRecord.voided_at.is_(None)).all())

    total = _empty()
    plan_part = _empty()
    extra_part = _empty()
    by_meal: dict[str, dict] = {}
    has_unknown = False
    extra_records = 0

    for record in records:
        values = _record_values(record)
        _add(total, values)
        meal_type = (record.meal_type or "").strip().lower()
        origin = (getattr(record, "origin", None) or "").strip().lower()

        if origin == "extra":
            _add(extra_part, values)
            extra_records += 1
            continue
        if origin == "plan" or (not origin and meal_type in MEAL_TASK):
            # 明确属于方案内，或早期记录（那时还没有归属字段，写了餐次就是按餐吃的）
            _add(plan_part, values)
            if meal_type in MEAL_TASK:
                bucket = by_meal.setdefault(meal_type, {**_empty(), "records": 0})
                _add(bucket, values)
                bucket["records"] += 1
            else:
                has_unknown = True
            continue

        # 没写归属也没写餐次：分不清是方案内还是额外吃，只能计入总量
        _add(extra_part, values)
        extra_records += 1
        has_unknown = True

    done = done_meal_tasks(db, user_id, day)
    done_keys = {f"meal_{meal_type}" for meal_type in done}

    # 兜底：任务没带 plan_day_id（早期数据）时，用当天方案天的餐食
    fallback_meals = plan_meal_nutrition(day_context.active_plan_day(db, user_id, day))
    plan_cache: dict[int, dict] = {}

    estimated_meals: list[str] = []
    for meal_type, task_key in MEAL_TASK.items():
        if task_key not in done_keys:
            continue
        if meal_type in by_meal:          # 这餐用户自己记过，用记录，不重复算
            continue
        if has_unknown:                   # 有记录没写餐次，判断不了哪餐记过了 → 宁可少算
            continue
        nutrition_values = None
        for plan_day_id in done.get(meal_type, []):
            if plan_day_id not in plan_cache:
                plan_cache[plan_day_id] = plan_meal_nutrition(
                    db.get(PlanDay, plan_day_id)
                )
            nutrition_values = plan_cache[plan_day_id].get(meal_type)
            if nutrition_values:
                break
        nutrition_values = nutrition_values or fallback_meals.get(meal_type)
        if not nutrition_values:
            continue
        _add(total, nutrition_values)
        _add(plan_part, nutrition_values)
        estimated_meals.append(meal_type)

    def out(values: dict) -> dict:
        return {key: round(value, 1) for key, value in values.items()}

    # 份量基线：只算「有实际记录的那几餐」，不是全天方案总量。
    # 用全天会让「只吃了午餐、晚餐还没吃」变成份量差一大截——那是没吃，不是吃少了。
    baseline = _empty()
    for meal_type in by_meal:
        values = fallback_meals.get(meal_type)
        if values:
            _add(baseline, values)

    return {
        **out(total),
        "records": len(records),
        "estimated": bool(estimated_meals),
        "estimated_meals": estimated_meals,
        "estimated_labels": [MEAL_LABELS.get(m, m) for m in estimated_meals],
        # 拆分：方案内 vs 方案外（判定看 total，归因看这两块）
        "plan": out(plan_part),
        "extra": out(extra_part),
        # 方案该餐本来排了多少：plan − plan_baseline 才是用户份量偏离
        "plan_baseline": out(baseline) if any(baseline.values()) else None,
        "extra_records": extra_records,
        "attribution_uncertain": has_unknown,
        "note": (
            f"{'、'.join(MEAL_LABELS.get(m, m) for m in estimated_meals)}按方案菜品估算"
            if estimated_meals else None
        ),
    }
