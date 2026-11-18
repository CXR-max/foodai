"""体重记录表"""
from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, String

from app.db.session import Base


class WeightLog(Base):
    __tablename__ = "weight_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    weight_kg = Column(Float, nullable=False)
    recorded_date = Column(Date, nullable=True, index=True)
    note = Column(String(200), nullable=True)
    created_at = Column(DateTime, nullable=True, default=datetime.now)
