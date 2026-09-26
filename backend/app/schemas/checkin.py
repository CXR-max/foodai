"""打卡相关请求体"""
from pydantic import BaseModel, Field


class FromPlanIn(BaseModel):
    """把方案某天导入日历（生成待完成任务）"""
    plan_day_id: int


class ManualCheckInIn(BaseModel):
    """手动打卡（运动 / 补记饮食）"""
    check_type: str = Field(pattern="^(diet|exercise)$")
    check_date: str | None = None                 # YYYY-MM-DD，不填默认今天
    exercise_type: str | None = None
    duration_min: int | None = Field(default=None, gt=0, le=600)
    calories_burned: float | None = Field(default=None, ge=0)
    note: str = Field(default="", max_length=300)
