"""饮水记录表（一天可记多次，累计到每日目标）

单独建表而不是塞进 check_ins：一天要记 5~8 次，塞打卡表会让任务列表变脏。
"""
from datetime import datetime

from sqlalchemy import Column, Date, DateTime, ForeignKey, Index, Integer, String

from app.db.session import Base


class WaterLog(Base):
    __tablename__ = "water_logs"
    __table_args__ = (Index("ix_water_logs_user_date", "user_id", "log_date"), )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    log_date = Column(Date, nullable=False, index=True)
    ml = Column(Integer, nullable=False, default=0)
    source = Column(String(20), nullable=True, default="manual")   # manual / device
    created_at = Column(DateTime, nullable=True, default=datetime.now)
