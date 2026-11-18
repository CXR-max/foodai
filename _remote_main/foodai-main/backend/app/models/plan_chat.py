"""AI 对话历史：长期保存用户与方案顾问的聊天，供方案约束抽取使用。"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text

from app.db.session import Base


class PlanChatMessage(Base):
    __tablename__ = "plan_chat_messages"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(20), nullable=False)       # user / assistant
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        Index("ix_plan_chat_user_created", "user_id", "created_at"),
    )
