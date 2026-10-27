"""积分商城奖励表：商品由 seed 脚本预置（见 seed.py），想加奖励直接改 seed 或后台插数据"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from app.db.session import Base


class Reward(Base):
    __tablename__ = "rewards"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    category = Column(String(20), nullable=False)  # honor=虚拟荣誉 / privilege=产品权益 / goods=实物券
    description = Column(String(300), default="")
    points_cost = Column(Integer, nullable=False)  # 兑换所需积分
    stock = Column(Integer, default=-1)            # 库存，-1=无限
    icon = Column(String(10), default="🎁")        # emoji 图标
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
