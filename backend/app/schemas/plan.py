"""养生方案相关请求体"""
from pydantic import BaseModel, Field


class GeneratePlanIn(BaseModel):
    days: int = Field(default=7, ge=1, le=7, description="方案天数")
    note: str = Field(default="", max_length=200, description="补充要求，如'下周想清淡一点'")
    # 前端"偏好面板"当场选择（不填则沿用健康档案里的值）
    taste_preferences: list[str] | None = None
    exercise_preferences: list[str] | None = None
    dietary_restrictions: list[str] | None = None
    # 前端"文字对话"里用户说过的话，与上面面板信息合并后一起生成
    chat_text: str = Field(default="", max_length=2000, description="用户在对话中补充的需求")


class RevisePlanIn(BaseModel):
    """对已生成的方案做"文字微调"：模型基于当前 JSON 原地改，其余天不动"""
    instruction: str = Field(min_length=1, max_length=500, description="修改意见，如'第3天午餐换成鱼'")
    day_index: int | None = Field(default=None, ge=1, le=7, description="只改某一天；不填则可改整份")


class ChatMessageIn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=1000)


class PlanChatIn(BaseModel):
    """生成方案前的"纯文字"偏好交流（无状态，历史由前端携带）"""
    messages: list[ChatMessageIn] = Field(default_factory=list)


class PlanMealItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    portion_g: float = Field(default=100, gt=0)


class PlanMealIn(BaseModel):
    """编辑方案天时的某一餐"""
    meal_type: str = Field(pattern="^(breakfast|lunch|dinner|snack)$")
    name: str = ""
    items: list[PlanMealItemIn] = []
    calories: float | None = None    # 不填则按营养库按菜名重算


class PlanExerciseIn(BaseModel):
    type: str = Field(min_length=1, max_length=30)
    duration_min: int = Field(default=30, gt=0, le=300)
    calories_burned: int = Field(default=0, ge=0)
    tips: str = ""


class PlanDayUpdateIn(BaseModel):
    """用户编辑方案某一天（方案不满意可修改再导入日历）"""
    theme: str | None = None
    meals: list[PlanMealIn] | None = None
    exercise: PlanExerciseIn | None = None
