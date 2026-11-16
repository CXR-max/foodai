"""养生方案主表 + 每日明细表"""
from datetime import datetime

from sqlalchemy import JSON, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text

from app.db.session import Base


class DietPlan(Base):
    __tablename__ = "diet_plans"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(100), nullable=True)
    status = Column(String(20), nullable=True)  # active / archived
    start_date = Column(Date, nullable=True)
    summary = Column(Text, nullable=True)
    daily_target_calories = Column(Integer, nullable=True)
    targets = Column(JSON, nullable=True)      # 计划级每日目标（热量区间/蛋白/饮水/睡眠/运动消耗）
    provider = Column(String(20), nullable=True)  # 云端 / 本地 / mock
    basis_snapshot = Column(JSON, nullable=True)  # 生成时的档案上下文留档
    reused_from = Column(Integer, nullable=True)  # 沿用的原方案 id（"再排一周"用）
    plan_version = Column(Integer, nullable=True, default=1)
    review = Column(JSON, nullable=True)       # 停用（归档）时的执行评价：分数/等级/亮点/建议
    created_at = Column(DateTime, nullable=True, default=datetime.now)


class PlanDay(Base):
    __tablename__ = "plan_days"

    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("diet_plans.id"), nullable=False, index=True)
    day_index = Column(Integer, nullable=False)
    date = Column(Date, nullable=True)
    meals = Column(JSON, nullable=True)
    exercise = Column(JSON, nullable=True)     # 主项（兼容旧前端/旧数据）
    exercises = Column(JSON, nullable=True)    # 完整运动处方（主项 + 拉伸）
    sleep = Column(JSON, nullable=True)        # 睡眠方案（作息/时长/建议）
    water_ml = Column(Integer, nullable=True)  # 当日饮水目标
    targets = Column(JSON, nullable=True)      # 当日目标覆盖（热量上下限/运动消耗下限）
    estimated_calories = Column(Float, nullable=True)
    theme = Column(String(50), nullable=True)
    edit_count = Column(Integer, nullable=True, default=0)          # 用户改过几次（餐食/运动/睡眠都算）
    last_edited_at = Column(DateTime, nullable=True)                # 最后一次修改时间（评价里算调整质量）
