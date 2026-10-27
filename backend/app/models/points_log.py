"""积分流水表（加分/扣分的每一笔记录，同时是防刷去重的依据）"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.db.session import Base


class PointsLog(Base):
    __tablename__ = "points_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action = Column(String(30), nullable=False)
    points = Column(Integer, nullable=False)
    description = Column(String(200), nullable=True)
    ref_type = Column(String(30), nullable=True)
    ref_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=True, default=datetime.now)
