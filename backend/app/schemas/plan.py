"""养生方案相关请求体"""
from datetime import date

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


class ChatMessageIn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=1000)


class PlanChatIn(BaseModel):
    """生成方案前的"纯文字"偏好交流（服务端持久化，前端同时带历史）"""
    messages: list[ChatMessageIn] = Field(default_factory=list)
    # 聊天上下文：当前方案/选中天/面板勾选（后端会再与长期状态合并）
    plan_id: int | None = None
    day_index: int | None = None
    taste_preferences: list[str] | None = None
    dietary_restrictions: list[str] | None = None


class PlanMealItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    portion_g: float = Field(default=100, gt=0)
    category: str | None = None          # 营养库类别（未收录时按同类均值估热量）
    calories: float | None = None        # 保留字段：命中营养库时以库值重算，不采信前端传值


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
    intensity: str = Field(default="moderate", pattern="^(light|moderate|intense)$")


class PlanDayUpdateIn(BaseModel):
    """用户编辑方案某一天（餐食/运动/睡眠/当日目标都可改）"""
    theme: str | None = None
    meals: list[PlanMealIn] | None = None
    exercise: PlanExerciseIn | None = None
    exercises: list[PlanExerciseIn] | None = None   # 完整运动处方（主项 + 拉伸）
    sleep: dict | None = None                       # 睡眠方案（作息/时长/建议）
    targets: dict | None = None                     # 当日目标覆盖（热量/饮水/运动消耗）
    water_ml: int | None = Field(default=None, ge=0, le=6000)
    # 改菜后热量超上限的处置：portion=按比例缩份量，exercise=折算成运动时长
    rebalance: str | None = Field(default=None, pattern="^(portion|exercise)$")


class PlanDayPreviewIn(BaseModel):
    """改菜试算（不落库）：只传餐食即可，其它字段忽略"""
    meals: list[PlanMealIn] = Field(default_factory=list)
    rebalance: str | None = Field(default=None, pattern="^(portion|exercise)$")


class ReusePlanIn(BaseModel):
    """沿用历史方案排下一周"""
    mode: str = Field(default="reframe", pattern="^(copy|reframe)$",
                      description="copy=原样复制；reframe=保留框架、重算餐食")
    start_date: date | None = Field(default=None, description="新方案开始日期，默认原方案结束次日")


class PlanImportIn(BaseModel):
    """把方案导入日程（原「导入日历」）：导入即生效，方案置为进行中"""
    mode: str = Field(default="week", pattern="^(day|week)$")
    day_index: int | None = Field(default=None, ge=1, le=7, description="mode=day 时指定第几天")
    anchor: date | None = Field(default=None, description="顺延基准日，默认今天；方案过期时整体平移到这天")
