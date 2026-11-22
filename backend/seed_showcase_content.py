"""为演示账号补齐「饮水」并生成一份可直接展示的七天方案（离线 / 幂等）。

背景
----
融合到主分支后，手机端「我的」页饮水环是 0 —— 主分支的饮水数据完全来自
``water_logs`` 表，而之前只补了饮食/手环/打卡/体重，这张表一直是空的。

本脚本做两件事，全部复用项目自身的业务逻辑，不伪造数据：

1. 饮水：按 ``wellness.add_water`` 走真实链路，为最近 7 天分多次记录，
   累计达到档案目标（55kg × 30ml = 1650ml）后自动完成饮水打卡。
2. 七天方案：调用主分支自带的 CP-SAT 约束求解器 ``planner.build_plan``，
   生成含餐食/运动/睡眠/饮水/每日目标/七项营养/宝塔分层的完整 7 天方案，
   status=active、start_date=今天，手机端「计划」页打开即「进行中」。
   provider 如实标为 solver，不冒充 AI 生成。

用法（backend 目录）：
    .venv\\Scripts\\python.exe seed_showcase_content.py
可重复执行：先清理本脚本产生的记录再重建。
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.check_in import CheckIn
from app.models.diet_plan import DietPlan, PlanDay
from app.models.health_profile import HealthProfile
from app.models.user import User
from app.models.water_log import WaterLog
from app.schemas.plan import GeneratePlanIn
from app.services import day_context, planner, plan_targets, wellness
from app.services.llm import foods_data
from app.services.plan_lifecycle import activate_plan
from app.routers.plans import build_profile_ctx

USERNAME = "demo_xiaoming"
DAYS = 7
WATER_SOURCE = "device"          # 用 device 标记脚本产生的数据，便于幂等清理
PLAN_TITLE = "七日均衡膳食方案（展示）"
# 每天 7 次饮水，合计 1850 ml：高于下限 1650，低于上限 2200，达标且不超量
WATER_PORTIONS = [250, 300, 250, 300, 250, 300, 200]


def seed_water(db, user: User, today: date) -> None:
    first = today - timedelta(days=DAYS - 1)
    # 幂等：只清本脚本标记的 device 记录，checkin 由 add_water 自行维护
    db.query(WaterLog).filter(
        WaterLog.user_id == user.id,
        WaterLog.log_date >= first,
        WaterLog.log_date <= today,
        WaterLog.source == WATER_SOURCE,
    ).delete(synchronize_session=False)
    db.flush()

    print(f"[饮水] 补 {first} ~ {today} 共 {DAYS} 天")
    for i in range(DAYS):
        day = today - timedelta(days=DAYS - 1 - i)
        targets = day_context.targets_for_day(db, user.id, day)
        target = int(targets.get("water_min_ml") or 2000)
        for ml in WATER_PORTIONS:
            wellness.add_water(db, user, day, ml, target,
                               source=WATER_SOURCE, targets=targets)
        total = wellness.water_total(db, user.id, day)
        task = (db.query(CheckIn)
                .filter(CheckIn.user_id == user.id, CheckIn.check_date == day,
                        CheckIn.task_key == "water").first())
        print(f"        {day}  {total} ml / 目标 {target} ml  "
              f"打卡={task.status if task else '无'}")
    db.commit()


def build_showcase_plan(db, user: User, profile: HealthProfile, today: date) -> None:
    # 幂等：删除同标题旧方案及其每日明细
    old = (db.query(DietPlan)
           .filter(DietPlan.user_id == user.id, DietPlan.title == PLAN_TITLE).all())
    for p in old:
        db.query(PlanDay).filter(PlanDay.plan_id == p.id).delete(synchronize_session=False)
        db.delete(p)
    db.flush()

    ctx = build_profile_ctx(profile, GeneratePlanIn(days=DAYS))
    targets = plan_targets.build_targets(ctx)

    # 展示用方案只从「简体自有库」选材排餐：全量活跃池里 1,498 条 TFDA 台湾
    # 食药署数据菜名是「中脂鮮羊乳」「紅心芭樂」这类繁体生僻条目，用于汇报
    # 不体面。这里只临时收窄求解器的候选池，不改营养库本身 —— 识别 / 查询
    # 仍是 1,850 条全量，用 try/finally 保证还原，不污染进程后续调用。
    original_load = foods_data.load_foods

    def zh_pool():
        return [f for f in original_load() if (f.get("source") or "") != "tfda"]

    foods_data.load_foods = zh_pool
    try:
        print(f"[方案] 排餐候选池（简体自有库） {len(zh_pool())} 条")
        result = planner.build_plan(ctx, DAYS)
    finally:
        foods_data.load_foods = original_load

    plan = DietPlan(
        user_id=user.id,
        title=PLAN_TITLE,
        status="active",
        start_date=today,
        summary=result.summary,
        daily_target_calories=int(result.daily_target_calories or ctx["daily_target_calories"]),
        targets=targets,
        plan_version=1,
        provider="solver",
        basis_snapshot=ctx,
    )
    db.add(plan)
    db.flush()

    for d in result.days:
        meals = [m.model_dump() for m in d.meals]
        est = round(sum(float(m.get("calories") or 0) for m in meals), 1)
        db.add(PlanDay(
            plan_id=plan.id,
            day_index=d.day_index,
            date=today + timedelta(days=d.day_index - 1),
            meals=meals,
            exercise=d.exercise.model_dump() if d.exercise else None,
            exercises=[e.model_dump() for e in (d.exercises or [])],
            sleep=d.sleep,
            targets=d.targets,
            water_ml=d.water_ml,
            estimated_calories=est,
            theme=d.theme,
            nutrition=d.nutrition,
            day_flags=d.day_flags,
        ))

    # 保证该用户只有这一份「进行中」，与 /plans/generate 的行为一致
    activate_plan(db, user.id, plan)
    db.commit()
    db.refresh(plan)

    days = (db.query(PlanDay).filter(PlanDay.plan_id == plan.id)
            .order_by(PlanDay.day_index).all())
    print(f"[方案] #{plan.id}「{plan.title}」 status={plan.status} "
          f"start={plan.start_date} provider={plan.provider} "
          f"目标 {plan.daily_target_calories} kcal / {len(days)} 天")
    for d in days:
        names = "、".join(m.get("name", "") for m in (d.meals or []))
        print(f"        D{d.day_index} {d.date} [{d.theme}] {d.estimated_calories:.0f} kcal "
              f"水 {d.water_ml} ml | {names[:60]}")
    print(f"        summary: {plan.summary[:160]}")


def main() -> None:
    today = date.today()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == USERNAME).first()
        if user is None:
            raise RuntimeError(f"找不到用户 {USERNAME}")
        profile = (db.query(HealthProfile)
                   .filter(HealthProfile.user_id == user.id).first())
        if profile is None:
            raise RuntimeError(f"{USERNAME} 没有健康档案，无法生成方案")

        # 方案先生成：饮水目标会通过 active_plan_day 与该方案对齐
        build_showcase_plan(db, user, profile, today)
        seed_water(db, user, today)
        foods_data.reload()

        # 复核：今天饮水总量与打卡、方案是否为进行中
        w = db.query(CheckIn).filter(
            CheckIn.user_id == user.id, CheckIn.check_date == today,
            CheckIn.task_key == "water").first()
        print(f"[复核] 今日饮水打卡 = {w.status if w else '无'}；"
              f"进行中方案 = {db.query(DietPlan).filter(DietPlan.user_id == user.id, DietPlan.status == 'active').count()} 份")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
