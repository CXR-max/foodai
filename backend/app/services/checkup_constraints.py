"""体检实测值 → 目标计算（L1 接合）

体检报告里最影响方案的其实不是化验项，而是三个体测数：体重、体脂率、基础代谢。
它们决定 BMR / TDEE，进而决定热量区间、蛋白克数、饮水目标。自报档案会过时、
会虚报，体检是称出来的。

只做覆盖，不做推断：
- 体重：体检必测，且是 BMR、TDEE、蛋白 g/kg、饮水 ml 的唯一输入，改一处全链条生效
- 基础代谢：只有体成分报告才有，有就盖掉 Mifflin-St Jeor 的估算值
- 体脂率：只在「减脂且不超重」时影响赤字（见 plan_targets.build_prescription）

边界：
- 只认 180 天内的体检，两三年前的体重不该定今天的目标
- 只影响「重新计算」的目标；已生成的方案快照（plan.targets）不动
- 不改档案、不改方案结构、不做食材排除（那一层留到后面）
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.models.checkup import Checkup

logger = logging.getLogger(__name__)

# 超过这个天数的体检不再参与目标计算
MAX_AGE_DAYS = 180

# 只接这三项：其余指标目前不改目标（要改需先想清楚临床依据，别凭感觉加）
MEASURED_KEYS = ("weight_kg", "body_fat_pct", "bmr_kcal")


def latest_measured(db: Session, user_id: int) -> dict:
    """最近一次体检里的体测实测值；没有或过期就返回空 dict。"""
    try:
        row = (
            db.query(Checkup)
            .filter(Checkup.user_id == user_id)
            .order_by(Checkup.check_date.desc(), Checkup.id.desc())
            .first()
        )
    except Exception as e:  # noqa: BLE001 —— 目标计算不能因为体检表读失败就整条挂掉
        logger.warning("读取体检实测值失败，按档案值计算：%s", e)
        return {}
    if not row:
        return {}

    check_date = row.check_date
    if check_date and (date.today() - check_date).days > MAX_AGE_DAYS:
        logger.info("体检 %s 已超过 %d 天，不参与目标计算", check_date, MAX_AGE_DAYS)
        return {}

    metrics = row.metrics or {}
    out: dict = {}
    for key in MEASURED_KEYS:
        try:
            value = float(metrics.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            out[key] = value
    if out and check_date:
        out["check_date"] = check_date.isoformat()
    return out


def apply_to_ctx(ctx: dict, measured: dict) -> dict:
    """把实测值覆盖进 profile_ctx（就地修改并返回）。

    覆盖的是「输入」，不是「结果」：weight_kg 一改，build_prescription 里的
    BMR、TDEE、basis_weight 与 build_targets 里的蛋白/饮水会一起跟着变。
    """
    if not measured:
        return ctx

    if measured.get("weight_kg"):
        ctx["weight_kg"] = measured["weight_kg"]
    if measured.get("body_fat_pct"):
        ctx["body_fat_pct"] = measured["body_fat_pct"]
    if measured.get("bmr_kcal"):
        ctx["bmr_measured"] = measured["bmr_kcal"]

    # 留痕：前端可据此说明「本次目标是按 X 月 X 日体检的实测体重算的」
    ctx["checkup_basis"] = {
        "check_date": measured.get("check_date"),
        "weight_kg": measured.get("weight_kg"),
        "body_fat_pct": measured.get("body_fat_pct"),
        "bmr_kcal": measured.get("bmr_kcal"),
    }
    logger.info("目标计算使用体检实测值：%s", ctx["checkup_basis"])
    return ctx
