"""睡眠记录表（一晚一条，手动记录与手环数据共用）

source 取值：
- manual          用户在日程页手动填的入睡/起床时间
- device          手环/健康 App 导入（本版先留字段，导入器后接）
- manual_override 用户在设备数据上手动修正过，之后设备数据不再覆盖
"""
from datetime import datetime

from sqlalchemy import (
    Column, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint,
)

from app.db.session import Base


class SleepLog(Base):
    __tablename__ = "sleep_logs"
    __table_args__ = (
        UniqueConstraint("user_id", "sleep_date", name="uq_sleep_user_date"),
        Index("ix_sleep_logs_user_date", "user_id", "sleep_date"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    sleep_date = Column(Date, nullable=False, index=True)   # 起床那天
    bedtime = Column(DateTime, nullable=True)
    wake_at = Column(DateTime, nullable=True)
    duration_min = Column(Integer, nullable=True)
    deep_min = Column(Integer, nullable=True)
    rem_min = Column(Integer, nullable=True)
    awake_min = Column(Integer, nullable=True)
    quality_score = Column(Integer, nullable=True)          # 主观质量 1~5
    source = Column(String(20), nullable=True, default="manual")
    plan_day_id = Column(Integer, ForeignKey("plan_days.id"), nullable=True)
    note = Column(String(200), nullable=True)
    created_at = Column(DateTime, nullable=True, default=datetime.now)
    updated_at = Column(DateTime, nullable=True, default=datetime.now, onupdate=datetime.now)
