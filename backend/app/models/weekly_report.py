"""AI 健康周报缓存表

为什么单独建表而不是内存缓存：
- 周报一次生成要调一次模型（1~3s + token 成本），同一周内结果基本不变，值得落库；
- 落库后可以回溯"上周模型给了什么建议"，也能做周与周的对比；
- 进程重启不丢，多实例部署天然共享。
"""
from datetime import datetime

from sqlalchemy import JSON, Column, Date, DateTime, Integer, String, Text, UniqueConstraint

from app.db.session import Base


class WeeklyReportCache(Base):
    __tablename__ = "weekly_report_cache"
    __table_args__ = (UniqueConstraint("user_id", "week_start", name="uq_weekly_report_user_week"), )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    week_start = Column(Date, nullable=False, index=True)   # 统计窗口起始日（今天 - 6 天）
    summary = Column(Text, nullable=True)
    score = Column(Integer, nullable=True)
    highlights = Column(JSON, nullable=True)
    suggestions = Column(JSON, nullable=True)
    source = Column(String(40), nullable=True)              # 生成来源：真模型名 / mock / rule
    created_at = Column(DateTime, nullable=True, default=datetime.now)
