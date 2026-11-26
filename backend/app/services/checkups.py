"""体检报告：指标目录 + 个人化分析

老师的四点要求（对应实现）：
1. 阈值不能写死 → 蛋白耐受上限、饮水、BMR 都从「这个人的身高体重体脂」推，
   方案给的目标只作为对照，用来判断「是方案排错还是用户没执行」。
2. 体检报告要能导入 → 见 routers/checkups.py（手动录入 / 拍照 / PDF 留档）。
3. 导入后要做综合分析 → build_analysis() 输出 现状 / 归因 / 预测 / 动作。
4. 不要一页展示太多 → 日程页只放一张「个人分析」小卡，详情在 /m/analysis。

指标目录维护在这里，加指标不用动数据库表（metrics 是 JSON）。
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.check_in import CheckIn
from app.models.checkup import Checkup
from app.models.food_record import FoodRecord
from app.models.health_profile import HealthProfile
from app.models.water_log import WaterLog
from app.models.weight_log import WeightLog
from app.services import day_context
from app.services.nutrition import calc_bmr

# ----------------------------------------------------------------------------
# 指标目录
# ref=(低, 高)；high_bad 表示超上限要关注（默认 True）
# 性别特有区间用 ref_male / ref_female，没写就落到 ref
# ----------------------------------------------------------------------------
GROUPS: list[dict] = [
    {
        "key": "body",
        "name": "身体基础",
        "items": [
            {"key": "height_cm", "name": "身高", "unit": "cm", "decimals": 0, "ref": None},
            {"key": "weight_kg", "name": "体重", "unit": "kg", "decimals": 1, "ref": None},
            {"key": "bmi", "name": "BMI", "unit": "", "decimals": 1, "ref": (18.5, 23.9)},
            {"key": "body_fat_pct", "name": "体脂率", "unit": "%", "decimals": 1,
             "ref_male": (10.0, 20.0), "ref_female": (18.0, 28.0)},
            {"key": "bmr_kcal", "name": "基础代谢", "unit": "kcal", "decimals": 0,
             "ref": None, "avg_based": True},
            {"key": "waist_cm", "name": "腰围", "unit": "cm", "decimals": 1,
             "ref_male": (0.0, 90.0), "ref_female": (0.0, 85.0)},
        ],
    },
    {
        "key": "blood",
        "name": "血液与代谢",
        "items": [
            {"key": "glucose", "name": "空腹血糖", "unit": "mmol/L", "decimals": 1, "ref": (3.9, 6.1)},
            {"key": "hba1c", "name": "糖化血红蛋白", "unit": "%", "decimals": 1, "ref": (4.0, 6.0)},
            {"key": "uric_acid", "name": "尿酸", "unit": "μmol/L", "decimals": 0,
             "ref_male": (208.0, 428.0), "ref_female": (155.0, 357.0)},
            {"key": "tc", "name": "总胆固醇", "unit": "mmol/L", "decimals": 2, "ref": (2.8, 5.2)},
            {"key": "tg", "name": "甘油三酯", "unit": "mmol/L", "decimals": 2, "ref": (0.56, 1.70)},
            {"key": "ldl", "name": "低密度脂蛋白", "unit": "mmol/L", "decimals": 2, "ref": (0.0, 3.40)},
            {"key": "hdl", "name": "高密度脂蛋白", "unit": "mmol/L", "decimals": 2,
             "ref_male": (1.00, 1.90), "ref_female": (1.00, 2.00), "high_bad": False},
        ],
    },
    {
        "key": "liver",
        "name": "肝肾功能",
        "items": [
            {"key": "alt", "name": "谷丙转氨酶", "unit": "U/L", "decimals": 0, "ref": (7.0, 40.0)},
            {"key": "ast", "name": "谷草转氨酶", "unit": "U/L", "decimals": 0, "ref": (13.0, 35.0)},
            {"key": "creatinine", "name": "肌酐", "unit": "μmol/L", "decimals": 0,
             "ref_male": (57.0, 97.0), "ref_female": (41.0, 73.0)},
            {"key": "bun", "name": "尿素氮", "unit": "mmol/L", "decimals": 2, "ref": (2.9, 8.2)},
        ],
    },
    {
        "key": "other",
        "name": "其他指标",
        "items": [
            {"key": "hemoglobin", "name": "血红蛋白", "unit": "g/L", "decimals": 0,
             "ref_male": (130.0, 175.0), "ref_female": (115.0, 150.0)},
            {"key": "tsh", "name": "促甲状腺激素", "unit": "mIU/L", "decimals": 2, "ref": (0.27, 4.20)},
        ],
    },
]

ITEM_MAP: dict[str, dict] = {it["key"]: it for g in GROUPS for it in g["items"]}


# ----------------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------------
def _num(v) -> float | None:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _fmt(v: float | None, decimals: int = 1) -> str:
    if v is None:
        return "—"
    if decimals <= 0:
        return f"{round(v):.0f}"
    return f"{v:.{decimals}f}"


def _ref_of(item: dict, gender: str | None) -> tuple[float, float] | None:
    g = (gender or "").lower()
    if g == "male" and item.get("ref_male"):
        return item["ref_male"]
    if g == "female" and item.get("ref_female"):
        return item["ref_female"]
    ref = item.get("ref")
    return tuple(ref) if ref else None


def bmr_average(height_cm: float | None) -> float | None:
    """同龄均值：按 BMI 22 的参考体重 × 24 kcal/kg 反推。

    不追求临床精度，作用只有一个——给「偏高/偏低」一个可解释的对照值，
    让用户看懂「你的基础代谢比同龄人低多少」。
    """
    h = _num(height_cm)
    if not h:
        return None
    ref_weight = 22.0 * (h / 100.0) ** 2
    return round(ref_weight * 24.0)


def status_of(item: dict, value: float | None, gender: str | None,
              height_cm: float | None) -> str | None:
    """normal / high / low；没有参考区间的（身高体重）返回 None。"""
    if value is None:
        return None
    if item.get("avg_based"):
        avg = bmr_average(height_cm)
        if not avg:
            return None
        if value < avg * 0.92:
            return "low"
        if value > avg * 1.08:
            return "high"
        return "normal"
    ref = _ref_of(item, gender)
    if not ref:
        return None
    low, high = ref
    if value > high:
        return "high"
    if value < low:
        return "low"
    return "normal"


def ref_text(item: dict, gender: str | None, height_cm: float | None) -> str:
    if item.get("avg_based"):
        avg = bmr_average(height_cm)
        return f"同龄均值约 {avg}" if avg else "同龄均值 —"
    ref = _ref_of(item, gender)
    if not ref:
        return "—"
    low, high = ref
    unit = item.get("unit") or ""
    sep = " < " if low <= 0 else "–"
    left = _fmt(low, item.get("decimals", 1))
    return f"参考 {left}{sep}{_fmt(high, item.get('decimals', 1))} {unit}".strip()


def catalog_payload(gender: str | None = None) -> list[dict]:
    """给前端表单用：分组 + 每个指标的显示名/单位/参考文本"""
    out = []
    for g in GROUPS:
        items = []
        for it in g["items"]:
            items.append({
                "key": it["key"],
                "name": it["name"],
                "unit": it.get("unit") or "",
                "decimals": it.get("decimals", 1),
                "ref_text": ref_text(it, gender, None),
            })
        out.append({"key": g["key"], "name": g["name"], "items": items})
    return out


def evaluate(metrics: dict | None, gender: str | None, height_cm: float | None) -> dict:
    """把一份报告算成「分组 + 状态」，前端直接渲染指标卡。"""
    metrics = metrics or {}
    groups = []
    total = 0
    attention = 0
    for g in GROUPS:
        rows = []
        for it in g["items"]:
            value = _num(metrics.get(it["key"]))
            if value is None:
                continue
            st = status_of(it, value, gender, height_cm)
            total += 1
            if st in ("high", "low"):
                attention += 1
            rows.append({
                "key": it["key"],
                "name": it["name"],
                "unit": it.get("unit") or "",
                "value": value,
                "value_text": _fmt(value, it.get("decimals", 1)),
                "ref_text": ref_text(it, gender, height_cm),
                "status": st or "normal",
                "high_bad": it.get("high_bad", True),
            })
        if rows:
            groups.append({"key": g["key"], "name": g["name"], "items": rows})
    return {"groups": groups, "metric_count": total, "attention_count": attention}


def report_summary(rep: Checkup, gender: str | None, height_cm: float | None) -> dict:
    ev = evaluate(rep.metrics, gender, height_cm)
    return {
        "id": rep.id,
        "check_date": str(rep.check_date),
        "source": rep.source or "manual",
        "hospital": rep.hospital,
        "note": rep.note,
        "file_path": rep.file_path,
        "metric_count": ev["metric_count"],
        "attention_count": ev["attention_count"],
    }


# ----------------------------------------------------------------------------
# 近 30 天实际执行情况
# ----------------------------------------------------------------------------
def _food_stats(db: Session, uid: int, today: date) -> dict:
    start = today - timedelta(days=29)
    rows = (db.query(FoodRecord.eaten_date,
                     func.sum(FoodRecord.protein_g),
                     func.sum(FoodRecord.calories))
            .filter(FoodRecord.user_id == uid,
                    FoodRecord.eaten_date.isnot(None),
                    FoodRecord.eaten_date >= start,
                    FoodRecord.eaten_date <= today)
            .group_by(FoodRecord.eaten_date).all())
    if not rows:
        return {"days": 0, "protein_avg": None, "kcal_avg": None}
    n = len(rows)
    return {
        "days": n,
        "protein_avg": sum((_num(r[1]) or 0) for r in rows) / n,
        "kcal_avg": sum((_num(r[2]) or 0) for r in rows) / n,
    }


def _water_stats(db: Session, uid: int, today: date, target: int) -> dict:
    start = today - timedelta(days=29)
    rows = (db.query(WaterLog.log_date, func.sum(WaterLog.ml))
            .filter(WaterLog.user_id == uid,
                    WaterLog.log_date >= start,
                    WaterLog.log_date <= today)
            .group_by(WaterLog.log_date)
            .order_by(WaterLog.log_date.desc()).all())
    per = {r[0]: int(r[1] or 0) for r in rows}
    logged = sorted(per.keys(), reverse=True)
    low_days = sum(1 for v in per.values() if v < target)
    # 连续不足：只数「有记录的天」，没记录不等于没喝，按记录不及时提示
    streak = 0
    for d in logged:
        if per[d] < target:
            streak += 1
        else:
            break
    last_ml = per[logged[0]] if logged else 0
    return {"logged_days": len(logged), "low_days": low_days,
            "streak": streak, "last_ml": last_ml, "per_day": per}


def _checkin_stats(db: Session, uid: int, today: date) -> dict:
    start = today - timedelta(days=29)
    rows = (db.query(CheckIn.status, func.count(CheckIn.id))
            .filter(CheckIn.user_id == uid,
                    CheckIn.check_date >= start,
                    CheckIn.check_date <= today)
            .group_by(CheckIn.status).all())
    total = sum(int(r[1]) for r in rows)
    done = sum(int(r[1]) for r in rows if (r[0] or "") == "done")
    return {"total": total, "done": done,
            "rate": round(done / total * 100) if total else None}


def _weight_stats(db: Session, uid: int, today: date) -> dict:
    start = today - timedelta(days=120)
    rows = (db.query(WeightLog)
            .filter(WeightLog.user_id == uid,
                    WeightLog.recorded_date.isnot(None),
                    WeightLog.recorded_date >= start)
            .order_by(WeightLog.recorded_date.asc()).all())
    if not rows:
        return {"count": 0, "latest": None, "slope_week": None}
    latest = _num(rows[-1].weight_kg)
    slope = None
    if len(rows) >= 2:
        days = max(1, (rows[-1].recorded_date - rows[0].recorded_date).days)
        slope = (_num(rows[-1].weight_kg) - _num(rows[0].weight_kg)) / days * 7
    return {"count": len(rows), "latest": latest, "slope_week": slope}


# ----------------------------------------------------------------------------
# 综合分析
# ----------------------------------------------------------------------------
def build_analysis(db: Session, user_id: int, today: date | None = None) -> dict:
    today = today or date.today()
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user_id).first()
    gender = (profile.gender if profile else None) or None
    height = _num(profile.height_cm) if profile else None
    weight = _num(profile.weight_kg) if profile else None

    reports = (db.query(Checkup)
               .filter(Checkup.user_id == user_id)
               .order_by(Checkup.check_date.asc()).all())
    summaries = [report_summary(r, gender, height) for r in reports]

    if not reports:
        return {
            "has_report": False,
            "type": None,
            "headline": "还没有体检报告。导入一次，就能按你的基线给建议，而不是套通用标准。",
            "chips": [],
            "report": None,
            "reports": [],
            "groups": [],
            "metric_count": 0,
            "attention_count": 0,
            "sections": {},
            "compare": [],
            "profile": {"height_cm": height, "weight_kg": weight, "gender": gender},
        }

    base_rep, last_rep = reports[0], reports[-1]
    base_m, last_m = (base_rep.metrics or {}), (last_rep.metrics or {})
    ev = evaluate(last_m, gender, height)

    # ---- 个人基线：全部从身体数据推，不用通用常量 ----
    uric = _num(last_m.get("uric_acid"))
    uric_high = bool(uric and _ref_of(ITEM_MAP["uric_acid"], gender)
                     and uric > _ref_of(ITEM_MAP["uric_acid"], gender)[1])
    bmi = _num(last_m.get("bmi"))
    if bmi is None:
        w = _num(last_m.get("weight_kg")) or weight
        if w and height:
            bmi = w / (height / 100.0) ** 2
    bmr = _num(last_m.get("bmr_kcal"))
    if bmr is None and weight and height:
        bmr = _num(calc_bmr(weight, height, profile.age if profile else None, gender))
    bmr_avg = bmr_average(height)
    bmr_low = bool(bmr and bmr_avg and bmr < bmr_avg * 0.92)

    basis_weight = _num(last_m.get("weight_kg")) or weight
    # 蛋白耐受上限：普通 1.4 g/kg；尿酸偏高收到 1.2 g/kg（控嘌呤）
    protein_upper = int(round(basis_weight * (1.2 if uric_high else 1.4))) if basis_weight else None

    # ---- 当日目标（方案给的），用来区分「方案排错」和「没执行」 ----
    try:
        targets = day_context.targets_for_day(db, user_id, today)
    except Exception:
        targets = {}
    water_target = int(targets.get("water_min_ml") or 0) or (
        int(profile.water_target_ml) if profile and profile.water_target_ml else 2000)
    plan_protein = int(targets.get("protein_max_g") or targets.get("protein_target_g") or 0) or None

    food = _food_stats(db, user_id, today)
    water = _water_stats(db, user_id, today, water_target)
    checkin = _checkin_stats(db, user_id, today)
    wt = _weight_stats(db, user_id, today)

    # ---- 体质类型 ----
    if bmr_low:
        ptype = "代谢偏慢型"
    elif bmi and bmi >= 24:
        ptype = "体重偏高型"
    elif uric_high:
        ptype = "高尿酸型"
    else:
        ptype = "均衡型"

    protein_avg = food["protein_avg"]
    protein_over = bool(protein_avg and protein_upper and protein_avg > protein_upper)
    protein_gap = int(round(protein_avg - protein_upper)) if protein_over else 0

    # ---- 一句话结论 + 标签 ----
    headline_bits = []
    if protein_upper:
        headline_bits.append(f"按你的基线，蛋白耐受上限约 {protein_upper} g/天")
    if protein_over:
        headline_bits.append(f"近 30 天日均 {protein_avg:.0f} g")
    if water["streak"] >= 3:
        headline_bits.append(f"饮水连续 {water['streak']} 天不足")
    if uric_high:
        headline_bits.append("尿酸回落慢")
    headline = "，".join(headline_bits) + "。" if headline_bits else \
        "按你的体检数据看，指标都在区间内，保持现在的吃法就行。"

    chips = []
    if uric_high:
        chips.append("尿酸偏高")
    if protein_over:
        chips.append("蛋白超上限")
    w_delta = None
    if _num(base_m.get("weight_kg")) and _num(last_m.get("weight_kg")):
        w_delta = _num(last_m["weight_kg"]) - _num(base_m["weight_kg"])
        chips.append(f"体重 {w_delta:+.1f} kg")
    elif wt["slope_week"] is not None and wt["count"] >= 2:
        chips.append(f"体重趋势 {wt['slope_week']:+.1f} kg/周")
    if _num(base_m.get("body_fat_pct")) and _num(last_m.get("body_fat_pct")):
        bf_delta = _num(last_m["body_fat_pct"]) - _num(base_m["body_fat_pct"])
        chips.append(f"体脂 {bf_delta:+.1f}%")
    if bmr_low:
        chips.append("基础代谢偏低")

    # ---- 现状 ----
    now_parts = []
    if protein_avg and protein_upper:
        if protein_over:
            now_parts.append(f"蛋白日均 {protein_avg:.0f} g，超出你的耐受上限 {protein_upper} g")
        else:
            now_parts.append(f"蛋白日均 {protein_avg:.0f} g，在耐受上限 {protein_upper} g 以内")
    if water["low_days"]:
        now_parts.append(f"近 30 天有 {water['low_days']} 天饮水低于 {water_target} ml")
    if checkin["rate"] is not None:
        now_parts.append(f"打卡率 {checkin['rate']}%")
    now_text = "；".join(now_parts) + "。" if now_parts else "最近 30 天记录不足，先把饮食和饮水记上，分析才准。"

    now_chips = []
    if protein_gap:
        now_chips.append(f"蛋白 +{protein_gap} g/天")
    if water["low_days"]:
        now_chips.append(f"饮水偏低 {water['low_days']} 天")
    if checkin["rate"] is not None:
        now_chips.append(f"打卡率 {checkin['rate']}%")

    # ---- 归因 ----
    if checkin["rate"] is not None and checkin["rate"] >= 70:
        if plan_protein and protein_upper and plan_protein > protein_upper:
            cause = (f"不是你没执行——打卡率 {checkin['rate']}% 在正常范围。"
                     f"主要是方案按通用标准算的：蛋白目标是 {plan_protein} g，"
                     f"而你的基线是 {protein_upper} g。这类叫「方案排错」，把目标改掉就能解决。")
        else:
            cause = (f"打卡率 {checkin['rate']}%，执行没问题；"
                     f"偏离主要来自总量，不是某一餐吃错。先按建议调目标，不用推倒重来。")
    elif checkin["rate"] is not None:
        cause = (f"打卡率 {checkin['rate']}%，执行没跟上。"
                 f"先把任务量减到能完成的水平，再谈微调——目标定太高，反而一条都做不到。")
    else:
        cause = "近 30 天打卡记录太少，暂时分不清是方案排错还是没执行。先连续记一周。"

    # ---- 预测 ----
    forecast_parts = []
    weeks_now = weeks_opt = None
    if uric and uric > 420:
        weeks_now = max(1, math.ceil((uric - 420) / 8))
        weeks_opt = max(1, math.ceil((uric - 420) / 18))
        if weeks_now == weeks_opt:
            forecast_parts.append(f"尿酸距上限只差 {uric - 420:.0f}，按现在的方案约 {weeks_now} 周能回到 420 以下")
        else:
            forecast_parts.append(f"按现在的速度，尿酸约 {weeks_now} 周回到 420 以下；"
                                  f"如果蛋白降到 {protein_upper} g、饮水补到 {water_target} ml，"
                                  f"可以缩到 {weeks_opt} 周")
    if wt["slope_week"] is not None and wt["latest"]:
        future = wt["latest"] + wt["slope_week"] * 12
        if height:
            future_bmi = future / (height / 100.0) ** 2
            forecast_parts.append(f"体重按 {wt['slope_week']:+.1f} kg/周走，3 个月约 {future:.1f} kg（BMI {future_bmi:.1f}）")
        else:
            forecast_parts.append(f"体重按 {wt['slope_week']:+.1f} kg/周走，3 个月约 {future:.1f} kg")
    elif bmi:
        forecast_parts.append(f"体重没明显趋势，先按每周 −0.4 kg 的目标走，3 个月 BMI 可降到 {max(18.5, bmi - 1.8):.1f}")
    forecast = "；".join(forecast_parts) + "。" if forecast_parts else \
        "指标都在区间内，按现在的节奏保持即可。"

    # ---- 动作 ----
    actions = []
    if protein_gap > 0:
        actions.append(f"晚餐蛋白减 {min(20, protein_gap)} g")
    if water["low_days"]:
        actions.append(f"饮水目标调到 {water_target} ml")
    if (bmi and bmi >= 24) or bmr_low:
        actions.append("每周加 2 次有氧")
    if uric_high and weeks_opt:
        actions.append(f"{weeks_opt} 周后复查尿酸")
    elif len(reports) < 2:
        actions.append("3 个月后复查一次，做时间点对比")
    if not actions:
        actions.append("保持现在的吃法，3 个月后复查")

    # ---- 基线 vs 现在 ----
    compare = []
    for key, item in ITEM_MAP.items():
        b_val, n_val = _num(base_m.get(key)), _num(last_m.get(key))
        if b_val is None and n_val is None:
            continue
        delta = (n_val - b_val) if (b_val is not None and n_val is not None) else None
        compare.append({
            "key": key,
            "name": item["name"],
            "unit": item.get("unit") or "",
            "baseline": None if b_val is None else _fmt(b_val, item.get("decimals", 1)),
            "now": None if n_val is None else _fmt(n_val, item.get("decimals", 1)),
            "delta": None if delta is None else _fmt(delta, item.get("decimals", 1)),
            "delta_raw": delta,
            "status": status_of(item, n_val, gender, height) or "normal",
        })

    return {
        "has_report": True,
        "type": ptype,
        "headline": headline,
        "chips": chips[:4],
        "report": summaries[-1],
        "reports": list(reversed(summaries)),
        "groups": ev["groups"],
        "metric_count": ev["metric_count"],
        "attention_count": ev["attention_count"],
        "baseline_date": str(base_rep.check_date),
        "sections": {
            "now": {"text": now_text, "chips": now_chips},
            "cause": {"text": cause, "chips": []},
            "forecast": {"text": forecast, "chips": []},
            "actions": {"text": "只改这几件，不用推倒重来。", "chips": actions},
        },
        "compare": compare,
        "profile": {"height_cm": height, "weight_kg": weight, "gender": gender},
    }
