"""AI 长期约束状态：当前有效约束的快照版本（旧版本保留审计）。"""
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Index, Integer, String

from app.db.session import Base


class PlanConstraintState(Base):
    __tablename__ = "plan_constraint_states"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    data = Column(JSON, nullable=False, default=dict)
    source_message_id = Column(Integer, nullable=True)
    scope = Column(String(20), default="long_term")   # long_term / week / session
    valid_from = Column(DateTime, default=datetime.now)
    valid_until = Column(DateTime, nullable=True)
    confidence = Column(Float, default=0.8)
    updated_at = Column(DateTime, default=datetime.now)
    superseded_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_plan_constraint_user_active", "user_id", "superseded_at"),
    )
