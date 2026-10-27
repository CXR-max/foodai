"""积分商城相关请求体"""
from pydantic import BaseModel, Field


class UseRedemptionIn(BaseModel):
    """使用权益类兑换券（补签卡等）"""
    check_date: str | None = Field(default=None, description="补签的目标日期 YYYY-MM-DD")
