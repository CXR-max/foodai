"""健康打卡（日历任务）表"""
from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, String

from app.db.session import Base


class CheckIn(Base):
    __tablename__ = "check_ins"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    check_date = Column(Date, nullable=True, index=True)
    check_type = Column(String(10), nullable=False)  # diet / exercise
    task_key = Column(String(30), nullable=True)     # 方案导入的任务标识
    status = Column(String(10), nullable=True)       # pending / done
    points_awarded = Column(Integer, nullable=True, default=0)
    food_record_id = Column(Integer, ForeignKey("food_records.id"), nullable=True)
    plan_day_id = Column(Integer, ForeignKey("plan_days.id"), nullable=True)
    exercise_type = Column(String(30), nullable=True)
    duration_min = Column(Integer, nullable=True)
    calories_burned = Column(Float, nullable=True)
    note = Column(String(300), nullable=True)
    created_at = Column(DateTime, nullable=True, default=datetime.now)
