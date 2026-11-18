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
    sleep_time = Column(String(5), nullable=True)      # 习惯入睡时间 HH:MM（排睡眠方案用）
    wake_time = Column(String(5), nullable=True)       # 习惯起床时间 HH:MM
    water_target_ml = Column(Integer, nullable=True)   # 自定义饮水目标（空则由体重推算）
    # 热量超限默认处理偏好：ask=手动选择（默认）/ portion=按比例缩份量 / exercise=加运动抵消
    over_limit_policy = Column(String(16), nullable=True, server_default="ask")
    updated_at = Column(DateTime, nullable=True, default=datetime.now)
