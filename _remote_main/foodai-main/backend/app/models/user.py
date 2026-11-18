"""用户表"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.db.session import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(50), nullable=False, unique=True, index=True)
    password_hash = Column(String(200), nullable=False)
    nickname = Column(String(50), nullable=True)
    points_total = Column(Integer, nullable=True, default=0)
    # 当前装备的头衔/徽章（成就体系，排行榜展示）；null = 未装备
    equipped_achievement_id = Column(Integer, ForeignKey("achievements.id"), nullable=True)
    created_at = Column(DateTime, nullable=True, default=datetime.now)
    updated_at = Column(DateTime, nullable=True, default=datetime.now)
