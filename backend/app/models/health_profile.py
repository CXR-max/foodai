"""健康档案表（每个用户一条）"""
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Integer, String

from app.db.session import Base


class HealthProfile(Base):
    __tablename__ = "health_profiles"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    height_cm = Column(Float, nullable=True)
    weight_kg = Column(Float, nullable=True)
    body_fat_pct = Column(Float, nullable=True)   # 体脂率 %（选填，用于运动消耗个性化计算）
    age = Column(Integer, nullable=True)
    gender = Column(String(10), nullable=True)
    activity_level = Column(String(20), nullable=True)
    goal = Column(String(20), nullable=True)
    target_weight_kg = Column(Float, nullable=True)
    daily_calorie_target = Column(Integer, nullable=True)
    taste_preferences = Column(JSON, nullable=True)
    dietary_restrictions = Column(JSON, nullable=True)
    exercise_preferences = Column(JSON, nullable=True)
    updated_at = Column(DateTime, nullable=True, default=datetime.now)
