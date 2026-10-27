"""积分商城奖励表"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from app.db.session import Base


class Reward(Base):
    __tablename__ = "rewards"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    category = Column(String(20), nullable=False)
    # 券的用途类型：makeup 补签卡 / custom_plan 方案定制券 / avatar_frame 头像框 / physical 实物券(线下核销)
    kind = Column(String(20), nullable=True, default="physical")
    description = Column(String(300), nullable=True)
    points_cost = Column(Integer, nullable=False)
    stock = Column(Integer, nullable=True)  # -1 表示不限量
    icon = Column(String(10), nullable=True)
    is_active = Column(Boolean, nullable=True, default=True)
    created_at = Column(DateTime, nullable=True, default=datetime.now)
