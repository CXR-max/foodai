"""结算三时点：半日快照 → 晚餐后定档 → 次日定稿

为什么一天要分三次判，而不是「随时判、随时处置」：
用户 14:00 只吃了早餐，热量才到 500，系统此时喊「热量不足 800」纯属添乱；
21:30 已经吃完晚饭，再让他「少吃点」也没东西可少了。
判定结论必须**跟时间走**，否则同一份数据在不同时刻给出相反结论，用户会觉得系统在瞎说。

三个时点（本地时间）：
- **13:00 半日快照**：只做半日体检。此时只判「吃得比方案多」（提前吃超了，
  后面几餐收得住就能拉回），不判「不足」——不足是还没吃完，不是吃错了。
- **20:00 晚餐后定档**：一天的主要餐次结束了，此时判「超」可靠，
  「不足」要再等到睡前（可能还有加餐/夜宵）。
- **次日 06:00 定稿**：过了这一夜，当天的数据是不可翻案的。
  定稿后才进「连续 3 天同向偏离」统计，避免把当天的临时状态当趋势。

窗口之外的状态：
- `pending`：这天还没到（未来日期），不判
- `open`：当天 13:00 之前，只提示「进行中」
- `midday` / `evening`：可处置，可改后续
- `final`：已定稿，只能改后续，不能改当天
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

# 时点分钟数（从 0 点起算）
MIDDAY_MIN = 13 * 60      # 13:00
EVENING_MIN = 20 * 60     # 20:00
FINAL_MIN = 6 * 60        # 次日 06:00

POINT_LABELS = {
    "pending": "未开始",
    "open": "上午",
    "midday": "半日",
    "evening": "晚餐后",
    "final": "已定稿",
}


def settlement_of(day: date, now: datetime | None = None) -> dict:
    """这天现在处于哪个结算时点，以及此刻允许做什么

    返回：
    - point: pending/open/midday/evening/final
    - settled: 是否已可下结论（open 时为 False）
    - can_adjust_today: 今天是否还有可改的餐次（过了 21:30 就没有了）
    - judge_under: 此刻是否该判「不足」（未吃完不判）
    """
    now = now or datetime.now()
    today = now.date()

    if day > today:
        return {
            "point": "pending", "label": POINT_LABELS["pending"], "settled": False,
            "can_adjust_today": False, "judge_under": False,
            "minutes_into_day": 0, "message": "这天还没到，不用判",
        }

    if day == today:
        minutes = now.hour * 60 + now.minute
    else:
        # 过去的日子：算「距离这天结束过了多久」
        minutes = 24 * 60 + (now.hour * 60 + now.minute) if day == today - timedelta(days=1) else 24 * 60 * 2

    if day < today:
        # 昨天：次日 06:00 之前还处在「夜间处置窗口」，可以给今天起的调整；
        # 06:00 之后彻底定稿
        if day == today - timedelta(days=1) and minutes < 24 * 60 + FINAL_MIN:
            return {
                "point": "evening", "label": POINT_LABELS["evening"], "settled": True,
                "can_adjust_today": False, "judge_under": True,
                "minutes_into_day": 24 * 60,
                "message": "昨晚结束，还没到定稿时间，可以调整今天起的安排",
            }
        return {
            "point": "final", "label": POINT_LABELS["final"], "settled": True,
            "can_adjust_today": False, "judge_under": True,
            "minutes_into_day": 24 * 60,
            "message": "这天的数据已定稿，只能影响后面几天",
        }

    # 当天
    if minutes < MIDDAY_MIN:
        return {
            "point": "open", "label": POINT_LABELS["open"], "settled": False,
            "can_adjust_today": True, "judge_under": False,
            "minutes_into_day": minutes,
            "message": "上午不判结论：还没吃完，只看有没有提前吃超",
        }
    if minutes < EVENING_MIN:
        return {
            "point": "midday", "label": POINT_LABELS["midday"], "settled": True,
            "can_adjust_today": True, "judge_under": False,
            "minutes_into_day": minutes,
            "message": "半日快照：只判吃超，不判不足（不足是还没吃完）",
        }
    if minutes < 21 * 60 + 30:
        return {
            "point": "evening", "label": POINT_LABELS["evening"], "settled": True,
            "can_adjust_today": True, "judge_under": True,
            "minutes_into_day": minutes,
            "message": "晚餐后定档：可以判足量，也还来得及散步或调整明天",
        }
    return {
        "point": "evening", "label": POINT_LABELS["evening"], "settled": True,
        "can_adjust_today": False, "judge_under": True,
        "minutes_into_day": minutes,
        "message": "已经 21:30 之后，今天不再加练也不建议补吃，只调后面几天",
    }


def is_final(day: date, now: datetime | None = None) -> bool:
    return settlement_of(day, now)["point"] == "final"


def final_date(day: date) -> date:
    """这天的定稿时刻落在哪天（次日）"""
    return day + timedelta(days=1)


def final_moment(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), time(hour=6, minute=0))


def days_since_final(day: date, now: datetime | None = None) -> int:
    """定稿后过了几天（0=还没定稿）"""
    now = now or datetime.now()
    moment = final_moment(day)
    if now < moment:
        return 0
    return (now.date() - moment.date()).days
