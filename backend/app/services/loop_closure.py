"""闭环：处置之后要能自我修正

一个只会「给建议」的系统和一个会「回看建议有没有用」的系统，差别在这里：

1. **采纳率**：提议的处置有多少被执行。低了说明建议不切实际（比如总让用户加练），
   高了说明手段选得对。这是把「处置质量」变成可观测数字的唯一办法。
2. **效果回看**：处置执行后 1~3 天，同一项偏离是否还在。还在 → 手段无效，
   下次对同一场景优先给别的手段；不在了 → 这个手段对这一类用户有效，记下来。
3. **例外率**：多少天挂着「聚餐/生病/出差」。超过 40% 说明判定口径太严，
   正常人不可能天天算得准——该放宽标准，而不是骂用户不自律。

不做的两件事（刻意）：
- 不用采纳率去"训练"模型或自动加码：措辞听起来像优化，实际会变成越推越激进。
- 不给用户看排名/分数：这是系统的自省，不是用户的考核。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.diet_deviation import DayMode, DietDeviation, PlanAdjustment
from app.services import day_context, diet_intake, nutrition_check, settlement

# 处置后观察几天算「效果」（太短看不到，太长没意义）
OUTCOME_WINDOW_DAYS = 2
# 例外天数占比超过这个值，说明判定口径偏严
EXCEPTION_RATIO_WARN = 0.40
# 采纳率低于这个值，说明提议不接地气，该换手段
ADOPTION_LOW = 0.35


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def adoption(db: Session, user_id: int, days: int = 14) -> dict:
    """处置采纳率：提议 vs 实际执行"""
    since = date.today() - timedelta(days=days)
    rows = (db.query(PlanAdjustment)
            .filter(PlanAdjustment.user_id == user_id,
                    PlanAdjustment.effective_date >= since)
            .all())
    proposed = [r for r in rows if r.status in ("proposed", "applied", "declined")]
    applied = [r for r in rows if r.status == "applied"]
    declined = [r for r in rows if r.status == "declined"]
    expired = [r for r in rows if r.status == "expired"]

    def rate(part: int, whole: int) -> float | None:
        return round(part / whole, 2) if whole else None

    by_kind: dict[str, dict] = {}
    for row in proposed:
        bucket = by_kind.setdefault(row.kind, {"proposed": 0, "applied": 0})
        bucket["proposed"] += 1
        if row.status == "applied":
            bucket["applied"] += 1
    for kind, bucket in by_kind.items():
        bucket["rate"] = rate(bucket["applied"], bucket["proposed"])

    overall = rate(len(applied), len(proposed))
    note = None
    if overall is not None and overall < ADOPTION_LOW and len(proposed) >= 3:
        note = (f"最近 {days} 天提议了 {len(proposed)} 次处置，执行 {len(applied)} 次。"
                "采纳率偏低不是你的问题——说明这些建议不够实用，会优先给「改餐食」而不是「加练」。")

    return {
        "days": days,
        "proposed": len(proposed),
        "applied": len(applied),
        "declined": len(declined),
        "expired": len(expired),
        "rate": overall,
        "by_kind": by_kind,
        "note": note,
    }


def exception_ratio(db: Session, user_id: int, days: int = 14) -> dict:
    """例外天数占比：判定口径严不严的体检指标"""
    since = date.today() - timedelta(days=days)
    rows = (db.query(DayMode)
            .filter(DayMode.user_id == user_id, DayMode.mode_date >= since)
            .all())
    counted = [r for r in rows if (r.mode or "normal") != "normal"]
    ratio = round(len(counted) / days, 2) if days else 0
    return {
        "days": days, "count": len(counted), "ratio": ratio,
        "modes": [{"date": str(r.mode_date), "mode": r.mode} for r in counted],
        "note": ("超过四成的天都挂着例外，说明正常日子的标准定得太紧，"
                 "该整体放宽而不是让你天天解释" if ratio > EXCEPTION_RATIO_WARN else None),
    }


def backfill_outcomes(db: Session, user_id: int, *, now: datetime | None = None) -> int:
    """给已执行的处置补效果：处置后这几天，同一项是否还在偏。

    只看「同一项、同方向、hard」的偏离——soft 是正常波动，不能算处置失败。
    """
    now = now or datetime.now()
    rows = (db.query(PlanAdjustment)
            .filter(PlanAdjustment.user_id == user_id,
                    PlanAdjustment.status == "applied",
                    PlanAdjustment.outcome.is_(None))
            .all())
    updated = 0
    for row in rows:
        if not row.effective_date:
            continue
        end = min(row.effective_date + timedelta(days=OUTCOME_WINDOW_DAYS), now.date() - timedelta(days=1))
        if end <= row.effective_date:
            continue   # 观察期还没结束

        dev = db.get(DietDeviation, row.deviation_id) if row.deviation_id else None
        nutrient = (dev.nutrient if dev else None)
        kind = (dev.kind if dev else None)

        window = (db.query(DietDeviation)
                  .filter(DietDeviation.user_id == user_id,
                          DietDeviation.dev_date > row.effective_date,
                          DietDeviation.dev_date <= end,
                          DietDeviation.severity == "hard")
                  .all())
        same = [d for d in window
                if (not nutrient or d.nutrient == nutrient) and (not kind or d.kind == kind)]
        others = [d for d in window if d not in same]

        row.outcome = {
            "window": [str(row.effective_date + timedelta(days=1)), str(end)],
            "resolved": len(same) == 0,
            "same_nutrient_hits": len(same),
            "other_nutrient_hits": len(others),
            "checked_at": now.isoformat(timespec="seconds"),
        }
        updated += 1
    if updated:
        db.flush()
    return updated


def effective_kinds(db: Session, user_id: int, *, direction: str | None = None) -> dict[str, dict]:
    """各类处置的历史命中率（用于下次优先推荐有效手段）"""
    rows = (db.query(PlanAdjustment)
            .filter(PlanAdjustment.user_id == user_id,
                    PlanAdjustment.status == "applied",
                    PlanAdjustment.outcome.isnot(None))
            .all())
    stats: dict[str, dict] = {}
    for row in rows:
        bucket = stats.setdefault(row.kind, {"tried": 0, "resolved": 0})
        bucket["tried"] += 1
        if (row.outcome or {}).get("resolved"):
            bucket["resolved"] += 1
    for bucket in stats.values():
        bucket["rate"] = round(bucket["resolved"] / bucket["tried"], 2) if bucket["tried"] else None
    return stats


def self_review(db: Session, user_id: int, *, now: datetime | None = None) -> dict:
    """系统自省卡：偏离趋势 / 处置采纳 / 例外占比 / 处置有效性"""
    from app.services import recalc

    backfill_outcomes(db, user_id, now=now)
    trend = recalc.trend(db, user_id)
    proposal = recalc.propose(db, user_id, now=now) if trend["direction"] != "none" else None
    adopt = adoption(db, user_id)
    exceptions = exception_ratio(db, user_id)
    kinds = effective_kinds(db, user_id)

    lines: list[str] = []
    if proposal:
        lines.append(proposal["message"])
    if adopt["note"]:
        lines.append(adopt["note"])
    if exceptions["note"]:
        lines.append(exceptions["note"])
    best = [k for k, v in kinds.items() if (v.get("rate") or 0) >= 0.6 and v["tried"] >= 2]
    if best:
        lines.append(f"对你有效的处置方式是：{'、'.join(best)}，之后会优先给这些。")

    return {
        "trend": trend,
        "proposal": proposal,
        "adoption": adopt,
        "exceptions": exceptions,
        "kinds": kinds,
        "lines": lines,
        "ok": not proposal,
    }


def day_state(db: Session, user_id: int, day: date, *, now: datetime | None = None) -> dict:
    """某天的完整状态：结算时点 + 模式 + 偏离 + 可处置项。

    给前端一次性拿到「现在能做什么」，避免它自己拼接口拼错。
    """
    from app.services import day_mode, disposition, metric_bands

    now = now or datetime.now()
    point = settlement.settlement_of(day, now)
    mode_effect = day_mode.effects(db, user_id, day)

    deviations = (db.query(DietDeviation)
                  .filter(DietDeviation.user_id == user_id, DietDeviation.dev_date == day)
                  .order_by(DietDeviation.id.desc())
                  .all())

    targets = day_context.targets_for_day(db, user_id, day)
    adjusted = day_mode.apply_to_targets(
        metric_bands.effective_calorie_band(targets), mode_effect,
        tdee=_num((targets.get("prescription") or {}).get("tdee")),
    )

    out = []
    for dev in deviations:
        if dev.status not in ("open",):
            continue
        opts = disposition.options(db, user_id, day, dev, adjusted, now=now)
        out.append({
            "deviation": {
                "id": dev.id, "kind": dev.kind, "nutrient": dev.nutrient,
                "actual": dev.actual, "limit": dev.limit_value, "delta": dev.delta,
                "attribution": dev.attribution,
                # 份量问题和方案排错要分开说：一个能矫正，一个只能改方案
                "attribution_text": metric_bands.ATTRIBUTION_TEXT.get(dev.attribution or "", ""),
                "severity": dev.severity,
                "status": dev.status,
                "label": nutrition_check.DIET_SPECS.get(dev.nutrient, ("",))[0],
            },
            "options": opts,
        })

    return {
        "date": str(day),
        "point": point,
        "mode": {**mode_effect},
        "deviations": out,
        "has_open": bool(out),
        "targets_note": adjusted.get("day_mode_note"),
    }
