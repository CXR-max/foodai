"""体重轨迹表：档案里只存"当前体重"，历史变化记录在这里（数据追踪页的体重曲线）"""
from datetime import date, datetime

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, String

from app.db.session import Base


class WeightLog(Base):
    __tablename__ = "weight_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    weight_kg = Column(Float, nullable=False)
    recorded_date = Column(Date, default=date.today, index=True)
    note = Column(String(200))
    created_at = Column(DateTime, default=datetime.now)
