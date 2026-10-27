"""健康档案相关请求体（所有字段可选 → 支持分次填写）"""
from pydantic import BaseModel, Field


class ProfileIn(BaseModel):
    height_cm: float | None = Field(default=None, gt=0, lt=300, description="身高cm")
    weight_kg: float | None = Field(default=None, gt=0, lt=500, description="体重kg")
    age: int | None = Field(default=None, gt=0, lt=120)
    gender: str | None = Field(default=None, pattern="^(male|female)$")
    activity_level: str | None = Field(default=None, pattern="^(sedentary|light|moderate|active|athlete)$")
    goal: str | None = Field(default=None, pattern="^(lose|maintain|gain)$")
    target_weight_kg: float | None = None
    taste_preferences: list[str] | None = None       # 口味偏好
    dietary_restrictions: list[str] | None = None    # 饮食禁忌
    exercise_preferences: list[str] | None = None    # 运动兴趣


class WeightIn(BaseModel):
    weight_kg: float = Field(gt=0, lt=500)
    recorded_date: str | None = None                 # YYYY-MM-DD，不填默认今天
    note: str = ""
