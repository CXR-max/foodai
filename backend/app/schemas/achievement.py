"""成就徽章相关请求体"""
from pydantic import BaseModel, Field


class EquipAchievementIn(BaseModel):
    """装备/卸下头衔或徽章（achievement_id 传空 = 卸下）"""
    achievement_id: int | None = Field(default=None, description="要装备的成就 id；null 表示卸下")
