"""健康档案表：与用户 1:1，AI 生成方案的依据（"偏好优先"就靠这三个偏好字段）"""
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Integer, String

from app.db.session import Base


class HealthProfile(Base):
    __tablename__ = "health_profiles"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, index=True, nullable=False)

    # ---- 基础身体指标 ----
    height_cm = Column(Float)                     # 身高 cm
    weight_kg = Column(Float)                     # 当前体重 kg
    age = Column(Integer)
    gender = Column(String(10))                   # male / female
    activity_level = Column(String(20), default="moderate")  # sedentary/light/moderate/active/athlete
    goal = Column(String(20), default="maintain")            # lose 减脂 / maintain 维持 / gain 增肌
    target_weight_kg = Column(Float)              # 目标体重
    daily_calorie_target = Column(Integer)        # 每日热量目标（后端按 BMR 公式计算，见 services/nutrition.py）

    # ---- 偏好（JSON 数组，存字符串列表）----
    taste_preferences = Column(JSON, default=list)      # 口味：["清淡","偏辣","偏甜","偏酸","偏咸"]
    dietary_restrictions = Column(JSON, default=list)   # 禁忌：["素食","忌辣","海鲜过敏","乳糖不耐","低糖"]
    exercise_preferences = Column(JSON, default=list)   # 运动兴趣：["跑步","游泳","瑜伽","骑行","力量训练","球类","快走","跳绳"]

    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
