"""体检报告表（一个用户可有多份，按体检日期区分）

老师的要求是「阈值不能写死，要从个人基线推出来」，所以体检数据必须能落库、
能按时间点对比（基线 vs 复查），而不是每次进来手填一遍。
"""
from datetime import datetime

from sqlalchemy import (
    JSON, Column, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint,
)

from app.db.session import Base


class Checkup(Base):
    __tablename__ = "health_checkups"
    __table_args__ = (
        # 同一天只留一份，重复导入按覆盖处理（体检报告通常一天一份）
        UniqueConstraint("user_id", "check_date", name="uq_checkup_user_date"),
        Index("ix_health_checkups_user_date", "user_id", "check_date"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    check_date = Column(Date, nullable=False, index=True)
    # manual 手动录入 / photo 拍照 / pdf 上传
    source = Column(String(16), nullable=True, default="manual")
    file_path = Column(String(300), nullable=True)   # 原图或 PDF 留档，便于回溯
    hospital = Column(String(100), nullable=True)
    # 指标存 JSON：{ "bmi": 24.1, "uric_acid": 421, ... }，
    # 加新指标不用改表结构（指标目录在 services/checkups.py 里维护）
    metrics = Column(JSON, nullable=True)
    note = Column(String(200), nullable=True)
    created_at = Column(DateTime, nullable=True, default=datetime.now)
    updated_at = Column(DateTime, nullable=True, default=datetime.now, onupdate=datetime.now)
