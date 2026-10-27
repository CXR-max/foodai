"""食物识别记录表"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String

from app.db.session import Base


class Recognition(Base):
    __tablename__ = "recognitions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    image_path = Column(String(300), nullable=True)
    provider = Column(String(20), nullable=True)  # 云端 / mock
    result = Column(JSON, nullable=True)          # 模型原始返回留档
    dish_name = Column(String(100), nullable=True)
    calories = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    is_food = Column(Boolean, nullable=True)
    food_record_id = Column(Integer, ForeignKey("food_records.id"), nullable=True)
    created_at = Column(DateTime, nullable=True, default=datetime.now)
