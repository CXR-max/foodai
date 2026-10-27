"""识别历史表：每次上传食物图片的 AI 识别结果

识别（拍照看看）和饮食记录（真的吃了）是两回事 → 分两张表；
识别后点"保存为饮食记录"才落 food_records。
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String

from app.db.session import Base


class Recognition(Base):
    __tablename__ = "recognitions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    image_path = Column(String(300))              # 图片相对路径（挂载在 /uploads 下）
    provider = Column(String(20))                 # 识别来源：mock / openai_compat
    result = Column(JSON)                         # 完整识别结果（VisionRecognitionResult + 营养计算）

    # ---- 冗余字段：列表页展示用，避免每次解析 JSON ----
    dish_name = Column(String(100))
    calories = Column(Float)
    confidence = Column(Float)
    is_food = Column(Boolean, default=True)       # False = 图片里没识别出食物
    food_record_id = Column(Integer, ForeignKey("food_records.id"))  # 保存为饮食记录后回填（防重复保存）

    created_at = Column(DateTime, default=datetime.now)
