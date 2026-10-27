"""奖励兑换记录表"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.db.session import Base


class Redemption(Base):
    __tablename__ = "redemptions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reward_id = Column(Integer, ForeignKey("rewards.id"), nullable=False)
    points_spent = Column(Integer, nullable=False)
    code = Column(String(20), nullable=False)  # 核销码
    status = Column(String(10), nullable=False, default="unused")  # unused / used
    used_at = Column(DateTime, nullable=True)   # 使用时间（权益类券核销时间）
    created_at = Column(DateTime, nullable=True, default=datetime.now)
