"""兑换记录表：兑换成功生成 8 位核销码（演示"线下领奖"场景）"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.db.session import Base


class Redemption(Base):
    __tablename__ = "redemptions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    reward_id = Column(Integer, ForeignKey("rewards.id"), nullable=False)
    points_spent = Column(Integer, nullable=False)
    code = Column(String(20), nullable=False)      # 8 位核销码
    status = Column(String(10), default="unused")  # unused / used
    created_at = Column(DateTime, default=datetime.now)
