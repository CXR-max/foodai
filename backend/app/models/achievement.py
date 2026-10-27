"""成就徽章体系表

设计：
- Achievement：徽章/头衔定义（code 唯一），condition_type + threshold 描述解锁条件
- UserAchievement：用户解锁记录（解锁即获得，无需积分购买）
- 装备：users.equipped_achievement_id 指向已解锁的成就，排行榜昵称旁展示
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.db.session import Base


class Achievement(Base):
    __tablename__ = "achievements"

    id = Column(Integer, primary_key=True)
    code = Column(String(40), nullable=False, unique=True, index=True)   # 如 streak_7
    name = Column(String(50), nullable=False)                            # 如 「七日恒心」
    icon = Column(String(10), nullable=True)                             # emoji
    description = Column(String(200), nullable=True)
    category = Column(String(10), nullable=False, default="badge")       # badge 徽章 / title 头衔
    # 条件类型：streak_days 连续打卡 / recognize_count 累计识别 /
    #           save_record_count 累计保存饮食 / points_total 累计积分 / plan_count 生成方案
    condition_type = Column(String(30), nullable=False)
    threshold = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=True, default=datetime.now)


class UserAchievement(Base):
    __tablename__ = "user_achievements"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    achievement_id = Column(Integer, ForeignKey("achievements.id"), nullable=False)
    unlocked_at = Column(DateTime, nullable=True, default=datetime.now)

    __table_args__ = (UniqueConstraint("user_id", "achievement_id", name="uq_user_achievement"),)
