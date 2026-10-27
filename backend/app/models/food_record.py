"""饮食记录表：用户真正吃了的东西（来源：拍照识别 / 手动录入 / 方案导入）
eaten_date 是按天聚合热量的关键字段（stats 图表全靠它）
"""
from datetime import date, datetime

from sqlalchemy import JSON, Column, Date, DateTime, Float, ForeignKey, Integer, String

from app.db.session import Base


class FoodRecord(Base):
    __tablename__ = "food_records"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    recognition_id = Column(Integer, ForeignKey("recognitions.id"))   # 来源识别（手动录入时为空）
    source = Column(String(10), default="manual")                     # photo=拍照 / manual=手动 / plan=方案导入

    dish_name = Column(String(100), nullable=False)
    ingredients = Column(JSON)                    # [{name, portion_g}]
    portion_g = Column(Float, default=100)        # 分量（克）
    portion_desc = Column(String(50))             # 分量描述，如"一碗约300g"

    # 该份的总营养（= 每100g营养 × 分量/100，由 services/nutrition.py 计算）
    calories = Column(Float, default=0)
    protein_g = Column(Float, default=0)
    fat_g = Column(Float, default=0)
    carb_g = Column(Float, default=0)

    meal_type = Column(String(10))                # breakfast / lunch / dinner / snack
    eaten_at = Column(DateTime, default=datetime.now)
    eaten_date = Column(Date, default=date.today, index=True)   # 所属日期（按天聚合的关键）
    image_path = Column(String(300))              # 从识别带过来的图片

    created_at = Column(DateTime, default=datetime.now)
