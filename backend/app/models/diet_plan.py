"""养生方案表：主表 diet_plans + 每日明细 plan_days（1:N）
meals/exercise 存 JSON → 用户"编辑方案"就是改这两份 JSON，灵活且不用改表结构
"""
from datetime import date, datetime

from sqlalchemy import JSON, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text

from app.db.session import Base


class DietPlan(Base):
    __tablename__ = "diet_plans"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    title = Column(String(100))                   # 如"7天方案 09-23"
    status = Column(String(20), default="active") # active / archived（归档）
    start_date = Column(Date, default=date.today)
    summary = Column(Text)                        # AI 的一句话总述
    daily_target_calories = Column(Integer)       # 每日热量目标
    provider = Column(String(20))                 # 生成来源：mock / openai_compat
    basis_snapshot = Column(JSON)                 # 生成时的档案+偏好快照（可追溯"为什么给我推荐这些"）
    created_at = Column(DateTime, default=datetime.now)


class PlanDay(Base):
    __tablename__ = "plan_days"

    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("diet_plans.id"), index=True, nullable=False)
    day_index = Column(Integer, nullable=False)   # 第几天（1~7）
    date = Column(Date)                           # 对应日期（start_date + day_index - 1）
    theme = Column(String(50), default="")        # 当天主题，如"轻盈开局"

    meals = Column(JSON)                          # [{meal_type, name, items:[{name,portion_g}], calories}]
    exercise = Column(JSON)                       # {type, duration_min, calories_burned, tips}，可为 null
    estimated_calories = Column(Float, default=0) # 当天总热量（三餐合计，编辑方案后重算）
