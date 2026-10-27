"""积分流水表：每一次积分变动都有据可查（防刷也靠它：查当日是否已有同 action 记录）"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.db.session import Base


class PointsLog(Base):
    __tablename__ = "points_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    action = Column(String(30), nullable=False)   # complete_profile/save_recognition/generate_plan/log_weight/task_meal/task_exercise/task_all_done_bonus/manual_checkin/redeem_reward
    points = Column(Integer, nullable=False)      # 正数=获得，负数=消费（兑换）
    description = Column(String(200), default="") # 展示文案，如"完成方案任务：午餐"
    ref_type = Column(String(30))                 # 关联对象类型（可选）
    ref_id = Column(Integer)                      # 关联对象 id（可选）
    created_at = Column(DateTime, default=datetime.now)
