"""饮食记录表"""
from datetime import datetime

from sqlalchemy import JSON, Column, Date, DateTime, Float, ForeignKey, Integer, String

from app.db.session import Base


class FoodRecord(Base):
    __tablename__ = "food_records"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    recognition_id = Column(Integer, ForeignKey("recognitions.id"), nullable=True)
    source = Column(String(10), nullable=True)  # recognition / manual
    dish_name = Column(String(100), nullable=False)
    ingredients = Column(JSON, nullable=True)
    portion_g = Column(Float, nullable=True)
    portion_desc = Column(String(50), nullable=True)
    calories = Column(Float, nullable=True)
    protein_g = Column(Float, nullable=True)
    fat_g = Column(Float, nullable=True)
    carb_g = Column(Float, nullable=True)
    meal_type = Column(String(10), nullable=True)  # breakfast/lunch/dinner/snack
    eaten_at = Column(DateTime, nullable=True)
    eaten_date = Column(Date, nullable=True, index=True)
    image_path = Column(String(300), nullable=True)
    created_at = Column(DateTime, nullable=True, default=datetime.now)
