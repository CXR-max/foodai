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
    provider = Column(String(20), nullable=True)  # 云端 / 本地 / mock
    basis_snapshot = Column(JSON, nullable=True)  # 生成时的档案上下文留档
    created_at = Column(DateTime, nullable=True, default=datetime.now)


class PlanDay(Base):
    __tablename__ = "plan_days"

    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("diet_plans.id"), nullable=False, index=True)
    day_index = Column(Integer, nullable=False)
    date = Column(Date, nullable=True)
    meals = Column(JSON, nullable=True)
    exercise = Column(JSON, nullable=True)
    estimated_calories = Column(Float, nullable=True)
    theme = Column(String(50), nullable=True)
