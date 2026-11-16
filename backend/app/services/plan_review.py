"""方案执行评价：方案停用（归档到历史方案）时，给这一期打个分并留下评语

四个维度，合计 100 分，每条都能对用户解释清楚：

- 打卡完成 40：由方案导入到日程的任务里，实际完成的比例
- 目标达成 35：热量落在区间 / 运动消耗达下限 / 睡眠够 / 饮水够，按有数据的天算达标率
  （四项内部权重：热量 45%、运动 20%、睡眠 20%、饮水 15%，无数据的维度不参与、权重归一化）
- 连续坚持 15：最长「当天任务全清」的连续天数 ÷ 有任务的天数
- 调整质量 10：改过菜的天里，改完仍落在热量区间的比例；一次没改记 8 分（照方案吃也算好）

没有导入也没有任何记录时返回 status="not_started"，不打分，只说明「这一期没有执行记录」。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.check_in import CheckIn
from app.models.diet_plan import DietPlan, PlanDay
from app.models.food_record import FoodRecord
from app.models.sleep_log import SleepLog
from app.models.water_log import WaterLog

logger = logging.getLogger("plan_review")

CHECKIN_MAX = 40
ADHERENCE_MAX = 35
STREAK_MAX = 15
ADJUST_MAX = 10

TASK_LABEL = {
    "meal_breakfast": "早餐", "meal_lunch": "午餐", "meal_dinner": "晚餐",
    "meal_snack": "加餐", "exercise": "运动", "sleep": "睡眠", "water": "饮水",
}
MEAL_KEYS = ("meal_breakfast", "meal_lunch", "meal_dinner", "meal_snack")

GRADES = ((90, "S"), (80, "A"), (70, "B"), (60, "C"), (0, "D"))


def _grade(score: int) -> str:
    for floor, name in GRADES:
        if score >= floor:
            return name
    return "D"


def _targets(plan: DietPlan, day: PlanDay) -> dict:
    merged = dict(plan.targets or {})
    merged.update(day.targets or {})
    return merged


def _ratio(hit: int, sample: int) -> float | None:
    return round(hit / sample, 3) if sample else None


def _longest_streak(full_days: list[date]) -> int:
    if not full_days:
        return 0
    longest = run = 1
    for prev, cur in zip(full_days, full_days[1:]):
        run = run + 1 if (cur - prev).days == 1 else 1
        longest = max(longest, run)
    return longest


def build_review(db: Session, plan: DietPlan, final: bool = True) -> dict:
    """算一份评价。final=False 表示方案还在进行中（只给进度，不写历史）"""
    days = (db.query(PlanDay).filter(PlanDay.plan_id == plan.id)
            .order_by(PlanDay.day_index.asc()).all())
    if not days:
        return {"plan_id": plan.id, "status": "empty", "score": None,
                "headline": "这份方案没有每日明细，无法评价。"}

    day_ids = [d.id for d in days]
    dates = [d.date for d in days if d.date]

    # 只统计落在方案日期内的任务：顺延/重复导入可能留下旧日期的任务，不能算进这一期
    query = db.query(CheckIn).filter(CheckIn.plan_day_id.in_(day_ids))
    if dates:
        query = query.filter(CheckIn.check_date.in_(dates))
    checkins = query.all()
    total = len(checkins)
    done = sum(1 for c in checkins if c.status == "done")

    per_day: dict[date, dict] = defaultdict(lambda: {"total": 0, "done": 0})
    by_key: dict[str, dict] = defaultdict(lambda: {"total": 0, "done": 0})
    for c in checkins:
        if c.check_date is None:
            continue
        per_day[c.check_date]["total"] += 1
        per_day[c.check_date]["done"] += 1 if c.status == "done" else 0
        key = c.task_key or "other"
        by_key[key]["total"] += 1
        by_key[key]["done"] += 1 if c.status == "done" else 0

    # ---- 实际记录（摄入 / 睡眠 / 饮水）----
    food_by_date: dict[date, float] = {}
    sleep_by_date: dict[date, int] = {}
    water_by_date: dict[date, int] = {}
    if dates:
        for eaten, cal in (db.query(FoodRecord.eaten_date, func.sum(FoodRecord.calories))
                           .filter(FoodRecord.user_id == plan.user_id,
                                   FoodRecord.eaten_date.in_(dates))
                           .group_by(FoodRecord.eaten_date).all()):
            food_by_date[eaten] = float(cal or 0)
        for sdate, dur in (db.query(SleepLog.sleep_date, SleepLog.duration_min)
                           .filter(SleepLog.user_id == plan.user_id,
                                   SleepLog.sleep_date.in_(dates)).all()):
            if dur:
                sleep_by_date[sdate] = int(dur)
        for wdate, ml in (db.query(WaterLog.log_date, func.sum(WaterLog.ml))
                          .filter(WaterLog.user_id == plan.user_id,
                                  WaterLog.log_date.in_(dates))
                          .group_by(WaterLog.log_date).all()):
            water_by_date[wdate] = int(ml or 0)

    has_record = bool(total or food_by_date or sleep_by_date or water_by_date)
    if not has_record:
        return {
            "plan_id": plan.id, "title": plan.title, "status": "not_started",
            "final": final, "score": None, "grade": None,
            "period": {"start": str(dates[0]) if dates else None,
                       "end": str(dates[-1]) if dates else None, "days": len(days)},
            "headline": "这一期没有执行记录：没把方案导入日程，也没记录饮食/睡眠/饮水。",
            "dimensions": [], "stats": {"days": len(days)},
            "highlights": [], "suggestions": ["下一期生成后先点「导入日程」，每天顺手打卡，才能看出执行情况。"],
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

    # ---- 1) 打卡完成 40 ----
    checkin_score = round(CHECKIN_MAX * done / total) if total else 0
    meal_done = sum(by_key[k]["done"] for k in MEAL_KEYS)
    meal_total = sum(by_key[k]["total"] for k in MEAL_KEYS)
    checkin_detail = (f"完成 {done}/{total} 项任务"
                      + (f"，其中餐食 {meal_done}/{meal_total}" if meal_total else "")) \
        if total else "这一期没导入日程，没有可打卡的任务"

    # ---- 2) 目标达成 35 ----
    cal_hit = cal_sample = 0
    ex_hit = ex_sample = 0
    sleep_hit = sleep_sample = 0
    water_hit = water_sample = 0
    ex_done_by_date: dict[date, bool] = defaultdict(bool)
    for c in checkins:
        if c.task_key == "exercise" and c.status == "done" and c.check_date:
            ex_done_by_date[c.check_date] = True

    for day in days:
        if not day.date:
            continue
        t = _targets(plan, day)
        lo, hi = t.get("calorie_min"), t.get("calorie_max")
        eaten = food_by_date.get(day.date)
        if eaten and lo and hi:
            cal_sample += 1
            if float(lo) <= eaten <= float(hi):
                cal_hit += 1

        if t.get("exercise_burn_min") and (day.exercises or day.exercise):
            ex_sample += 1
            if ex_done_by_date.get(day.date):
                ex_hit += 1

        need_sleep = t.get("sleep_min_h")
        dur = sleep_by_date.get(day.date)
        if need_sleep and dur:
            sleep_sample += 1
            if dur / 60 >= float(need_sleep) - 0.3:   # 允许 20 分钟误差
                sleep_hit += 1

        need_water = t.get("water_min_ml")
        ml = water_by_date.get(day.date)
        if need_water and ml is not None:
            water_sample += 1
            if ml >= float(need_water) * 0.9:
                water_hit += 1

    parts = [
        ("热量", cal_hit, cal_sample, 0.45),
        ("运动", ex_hit, ex_sample, 0.20),
        ("睡眠", sleep_hit, sleep_sample, 0.20),
        ("饮水", water_hit, water_sample, 0.15),
    ]
    used = [(label, _ratio(hit, sample), weight) for label, hit, sample, weight in parts if sample]
    if used:
        weight_sum = sum(w for _, _, w in used)
        adherence_rate = sum(rate * w for _, rate, w in used) / weight_sum
        adherence_score = round(ADHERENCE_MAX * adherence_rate)
        adherence_detail = "、".join(
            f"{label} {round(rate * 100)}%" for label, rate, _ in used
        )
    else:
        adherence_score = 0
        adherence_detail = "这一期没有饮食/睡眠/饮水记录，无法判断达成情况"

    # ---- 3) 连续坚持 15 ----
    full_days = sorted(d for d, v in per_day.items() if v["total"] and v["done"] == v["total"])
    task_days = [d for d, v in per_day.items() if v["total"]]
    longest = _longest_streak(full_days)
    streak_base = len(task_days) or 0
    streak_score = round(STREAK_MAX * longest / streak_base) if streak_base else 0
    streak_detail = (f"最长连续 {longest} 天全部完成 / 共 {streak_base} 天有任务"
                     if streak_base else "这一期没有导入任务，谈不上连续")

    # ---- 4) 调整质量 10 ----
    edited_days = [d for d in days if (d.edit_count or 0) > 0]
    if not edited_days:
        adjust_score = round(ADJUST_MAX * 0.8)
        adjust_detail = "没有改过餐食，按方案执行"
    else:
        ok_days = sum(
            1 for d in edited_days
            if ((d.targets or {}).get("nutrition_snapshot") or {}).get("status") == "ok"
        )
        adjust_score = round(ADJUST_MAX * ok_days / len(edited_days))
        adjust_detail = f"改过 {len(edited_days)} 天餐食，其中 {ok_days} 天改完仍在热量区间"

    score = max(0, min(100, checkin_score + adherence_score + streak_score + adjust_score))
    grade = _grade(score)

    dimensions = [
        {"key": "checkin", "label": "打卡完成", "score": checkin_score,
         "max": CHECKIN_MAX, "detail": checkin_detail},
        {"key": "adherence", "label": "目标达成", "score": adherence_score,
         "max": ADHERENCE_MAX, "detail": adherence_detail},
        {"key": "streak", "label": "连续坚持", "score": streak_score,
         "max": STREAK_MAX, "detail": streak_detail},
        {"key": "adjust", "label": "调整质量", "score": adjust_score,
         "max": ADJUST_MAX, "detail": adjust_detail},
    ]

    # ---- 亮点与建议 ----
    highlights: list[str] = []
    suggestions: list[str] = []
    if total and done / total >= 0.8:
        highlights.append(f"打卡完成率 {round(done / total * 100)}%，执行很自觉")
    if cal_sample >= 2 and cal_hit / cal_sample >= 0.7:
        highlights.append(f"{cal_hit}/{cal_sample} 天热量落在目标区间")
    if longest >= max(2, streak_base // 2) and streak_base:
        highlights.append(f"最长连续 {longest} 天清零全部任务")
    if sleep_sample and sleep_hit / sleep_sample >= 0.7:
        highlights.append("睡眠基本达标，作息稳定")

    if total and done / total < 0.6:
        suggestions.append(f"打卡只完成 {done}/{total}，下一期把「导入日程」当天就点开，任务会自己排好")
    if cal_sample and cal_hit / cal_sample < 0.5:
        suggestions.append("热量偏离目标的天数偏多，改菜后看一眼顶部营养条再保存")
    if ex_sample and ex_hit / ex_sample < 0.6:
        suggestions.append(f"运动只完成 {ex_hit}/{ex_sample} 天，把运动排在晚饭后立刻做更容易坚持")
    if sleep_sample and sleep_hit / sleep_sample < 0.6:
        suggestions.append("睡眠达标天数不足，先固定起床时间，比硬性早睡更容易做到")
    if water_sample and water_hit / water_sample < 0.6:
        suggestions.append("饮水常喝不够，把水杯放到手边、按打卡按钮顺手加 250ml")
    if not suggestions:
        suggestions.append("这一期执行得不错，下一期沿用同一套目标即可，不用加码")

    if not highlights:
        highlights.append(
            f"这一期完成了 {done}/{total} 项任务，已经开始动起来了" if total
            else "已经开始记录饮食/睡眠，有数据才能看出趋势"
        )

    headlines = {
        "S": "执行几乎没打折，这一期可以原样再来一遍",
        "A": "整体执行到位，个别项还能再稳一点",
        "B": "完成了一大半，重点补上最弱的那一项",
        "C": "执行断断续续，先把打卡习惯立起来",
        "D": "这一期基本没跟上，下一期先从「导入日程 + 每天打卡」开始",
    }

    return {
        "plan_id": plan.id, "title": plan.title,
        "status": "final" if final else "ongoing", "final": final,
        "score": score, "grade": grade, "headline": headlines[grade],
        "period": {"start": str(dates[0]) if dates else None,
                   "end": str(dates[-1]) if dates else None, "days": len(days)},
        "dimensions": dimensions,
        "stats": {
            "days": len(days),
            "tasks_total": total, "tasks_done": done,
            "task_rate": _ratio(done, total),
            "days_with_tasks": len(task_days),
            "full_days": len(full_days), "longest_streak": longest,
            "calorie_days": cal_sample, "calorie_hit_days": cal_hit,
            "exercise_days": ex_sample, "exercise_hit_days": ex_hit,
            "sleep_days": sleep_sample, "sleep_hit_days": sleep_hit,
            "water_days": water_sample, "water_hit_days": water_hit,
            "edited_days": len(edited_days),
            "avg_intake": round(sum(food_by_date.values()) / len(food_by_date)) if food_by_date else None,
            "avg_water_ml": round(sum(water_by_date.values()) / len(water_by_date)) if water_by_date else None,
            "avg_sleep_h": round(sum(sleep_by_date.values()) / len(sleep_by_date) / 60, 1) if sleep_by_date else None,
            "by_task": {k: {"label": TASK_LABEL.get(k, k), **v,
                            "rate": _ratio(v["done"], v["total"])}
                        for k, v in by_key.items()},
        },
        "highlights": highlights[:3],
        "suggestions": suggestions[:3],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def store_review(db: Session, plan: DietPlan) -> dict:
    """算完写进 diet_plans.review（归档/停用时调用，不提交事务）"""
    review = build_review(db, plan, final=True)
    plan.review = review
    logger.info("方案 %s 停用评价：%s 分（%s）", plan.id, review.get("score"), review.get("grade"))
    return review
