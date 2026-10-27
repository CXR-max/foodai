"""用户表：登录账号 + 冗余积分总额（排行榜直接按 points_total 排序，免去聚合查询）"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.db.session import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, index=True, nullable=False)  # 登录名
    password_hash = Column(String(200), nullable=False)                     # pbkdf2 哈希，见 core/security.py
    nickname = Column(String(50), default="")                               # 昵称（排行榜展示用，默认取用户名）
    points_total = Column(Integer, default=0)                               # 积分总额（与 points_logs 保持同步）
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
