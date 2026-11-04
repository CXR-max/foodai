"""AI 健康周报：汇总近 7 天数据 → 大模型生成点评与建议（失败降级规则版）

数据全部来自现有库表（food_records / check_ins / weight_logs / health_profile），
不新增表。生成走 provider.chat()，FallbackProvider 自带 mock 兜底；
解析失败时再退一层"纯规则版周报"，保证接口永远有内容返回。
"""
import logging
from datetime import date, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.check_in import CheckIn
from app.models.food_record import FoodRecord
from app.models.health_profile import HealthProfile
from app.models.user import User
from app.models.weight_log import WeightLog
from app.services.llm.base import LLMError, extract_json
from app.services.llm.provider import get_llm_provider

logger = logging.getLogger("weekly_report")


class WeeklyReport(BaseModel):
    """周报结构（大模型输出 / 规则版共用）"""
    summary: str = Field(description="一周总体点评，2~3 句")
    score: int = Field(default=60, ge=0, le=100, description="本周健康分 0-100")
    highlights: list[str] = Field(default_factory=list, description="做得好的地方")
    suggestions: list[str] = Field(default_factory=list, description="下周改进建议")
    source: str = "mock"   # 实际生成来源：真模型名 / mock


def collect_week_stats(db: Session, user: User) -> dict:
    """聚合近 7 天的用户数据，作为周报与海报共用的数据源"""
    today = date.today()
    start = today - timedelta(days=6)

    # 热量摄入：按天
    cal_rows = (db.query(FoodRecord.eaten_date, func.sum(FoodRecord.calories))
                .filter(FoodRecord.user_id == user.id,
                        FoodRecord.eaten_date >= start, FoodRecord.eaten_date <= today)
                .group_by(FoodRecord.eaten_date).all())
    cal_by_date = {d: v or 0 for d, v in cal_rows}
    days_with_records = len(cal_by_date)
    total_intake = sum(cal_by_date.values())
    avg_intake = round(total_intake / 7, 1)

    # 营养素日均（全周期口径：有记录天的均值 × 记录天占比）
    nut = (db.query(func.avg(FoodRecord.protein_g), func.avg(FoodRecord.fat_g),
                    func.avg(FoodRecord.carb_g))
           .filter(FoodRecord.user_id == user.id,
                   FoodRecord.eaten_date >= start, FoodRecord.eaten_date <= today)).first()
    factor = days_with_records / 7
    avg_protein = round((nut[0] or 0) * factor, 1)
    avg_fat = round((nut[1] or 0) * factor, 1)
    avg_carb = round((nut[2] or 0) * factor, 1)

    # 运动消耗：按天
    burn_rows = (db.query(CheckIn.check_date, func.sum(CheckIn.calories_burned))
                 .filter(CheckIn.user_id == user.id, CheckIn.check_type == "exercise",
                         CheckIn.status == "done",
                         CheckIn.check_date >= start, CheckIn.check_date <= today)
                 .group_by(CheckIn.check_date).all())
    burn_by_date = {d: v or 0 for d, v in burn_rows}
    total_burn = round(sum(burn_by_date.values()), 1)
    exercise_times = int(sum(1 for v in burn_by_date.values() if v > 0))

    # 打卡：完成天数 + 连续天数（复用 stats.calc_streak）
    from app.routers.stats import calc_streak
    done_days = {r[0] for r in db.query(CheckIn.check_date).filter(
        CheckIn.user_id == user.id, CheckIn.status == "done",
        CheckIn.check_date >= start, CheckIn.check_date <= today).distinct()}
    streak = calc_streak(db, user.id)

    # 体重变化
    weights = (db.query(WeightLog)
               .filter(WeightLog.user_id == user.id,
                       WeightLog.recorded_date >= start, WeightLog.recorded_date <= today)
               .order_by(WeightLog.recorded_date.asc()).all())
    weight_change = round(weights[-1].weight_kg - weights[0].weight_kg, 1) if len(weights) >= 2 else None

    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    target = profile.daily_calorie_target if profile else None

    return {
        "start": start, "end": today,
        "days_with_records": days_with_records,
        "cal_by_date": {str(d): round(v, 1) for d, v in cal_by_date.items()},
        "total_intake": round(total_intake, 1),
        "avg_intake": avg_intake,
        "target": target,
        "avg_protein_g": avg_protein, "avg_fat_g": avg_fat, "avg_carb_g": avg_carb,
        "total_burn": total_burn,
        "exercise_times": exercise_times,
        "burn_by_date": {str(d): round(v, 1) for d, v in burn_by_date.items()},
        "checkin_days": len(done_days),
        "streak_days": streak,
        "weight_change": weight_change,
        "goal": profile.goal if profile else "maintain",
    }


def _rule_based_report(s: dict) -> WeeklyReport:
    """无模型 / 解析失败时的确定性周报（保证演示不空转）"""
    highlights, suggestions = [], []
    avg, target = s["avg_intake"], s["target"]

    if s["checkin_days"] >= 5:
        highlights.append(f"本周打卡 {s['checkin_days']} 天，坚持得很好")
    else:
        suggestions.append(f"本周只打卡了 {s['checkin_days']} 天，建议至少坚持 5 天")
    if s["exercise_times"] >= 3:
        highlights.append(f"运动 {s['exercise_times']} 次，共消耗 {s['total_burn']} kcal")
    else:
        suggestions.append("运动偏少，建议每周至少 3 次、每次 30 分钟以上")
    if target and avg > target * 1.1:
        suggestions.append(f"日均摄入 {avg} kcal 高于目标 {target} kcal，注意控制主食与油的分量")
    elif target and avg < target * 0.8:
        suggestions.append(f"日均摄入 {avg} kcal 低于目标 {target} kcal，别为了减脂饿肚子")
    else:
        highlights.append("热量摄入与目标匹配良好，继续保持")
    if s["weight_change"] is not None:
        highlights.append(f"本周体重变化 {s['weight_change']:+} kg")
    if not suggestions:
        suggestions.append("当前节奏很健康，下周可以尝试记录得更完整（含加餐）")

    score = 40 + min(s["checkin_days"], 7) * 5 + min(s["exercise_times"], 5) * 4
    if target and abs(avg - target) <= target * 0.1:
        score += 10
    return WeeklyReport(
        summary=(f"本周记录 {s['days_with_records']} 天，日均摄入 {avg} kcal"
                 + (f"（目标 {target}）" if target else "")
                 + f"，运动消耗 {s['total_burn']} kcal，连续打卡 {s['streak_days']} 天。"),
        score=min(score, 95),
        highlights=highlights[:3], suggestions=suggestions[:3], source="mock",
    )


def generate_weekly_report(db: Session, user: User) -> WeeklyReport:
    s = collect_week_stats(db, user)

    # 一周几乎没数据 → 直接规则版，不浪费模型调用
    if s["days_with_records"] == 0 and s["checkin_days"] == 0:
        return WeeklyReport(
            summary="本周还没有任何饮食记录和打卡，先迈出第一步吧！",
            score=30,
            highlights=[],
            suggestions=["去「食物识别」记录第一餐", "在「健康打卡」完成一次运动打卡", "到「健康档案」完善身体数据"],
            source="mock",
        )

    goal_text = {"lose": "减脂", "maintain": "维持体重", "gain": "增肌"}.get(s["goal"], "维持体重")
    prompt = f"""你是营养师，请根据用户近 7 天的健康数据写一份中文周报，严格只输出 JSON（不要 markdown 代码块）：
{{"summary": "2~3 句总体点评", "score": 0-100 的健康分, "highlights": ["做得好的点，最多3条"], "suggestions": ["下周建议，最多3条"]}}

用户数据（{s['start']} ~ {s['end']}）：
- 目标：{goal_text}；每日热量目标：{s['target'] or '未设置'} kcal
- 有记录天数：{s['days_with_records']}/7，日均摄入 {s['avg_intake']} kcal
- 日均蛋白质 {s['avg_protein_g']}g / 脂肪 {s['avg_fat_g']}g / 碳水 {s['avg_carb_g']}g
- 运动 {s['exercise_times']} 次，总消耗 {s['total_burn']} kcal
- 打卡完成 {s['checkin_days']} 天，当前连续 {s['streak_days']} 天
- 本周体重变化：{s['weight_change']} kg"""

    try:
        provider = get_llm_provider()
        text = provider.chat(
            [{"role": "system", "content": "你是专业的营养师，输出严格为 JSON。"},
             {"role": "user", "content": prompt}],
            max_tokens=settings.LLM_REPORT_MAX_TOKENS,
        )
        data = extract_json(text)
        data.pop("source", None)
        report = WeeklyReport.model_validate(data)
        report.source = provider.active_name
        return report
    except Exception as e:  # noqa: BLE001 —— 周报是锦上添花，任何失败都降级规则版
        logger.warning("周报模型生成失败，使用规则版：%s", e)
        return _rule_based_report(s)
