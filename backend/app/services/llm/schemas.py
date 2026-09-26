"""
LLM 输出的结构化定义（Pydantic 模型）
注意：识别结果里【没有】热量/营养素字段 —— 视觉模型只负责"认菜+估克重"，
营养值由本地营养库（foods_data.json）计算，全站口径统一。
"""
from pydantic import BaseModel, Field


# ---------- 食物识别 ----------

class RecognizedIngredient(BaseModel):
    """识别出的单种食材"""
    name: str
    portion_g: float = Field(default=0, ge=0)  # 该食材估算克重


class VisionRecognitionResult(BaseModel):
    """视觉模型的识别结果"""
    is_food: bool = True                       # 图片里是否包含食物
    dish_name: str = ""                        # 菜品名（多道菜取最主要的一道）
    ingredients: list[RecognizedIngredient] = Field(default_factory=list)
    portion_g: float = Field(default=0, ge=0)  # 总分量估算（克）
    portion_desc: str = ""                     # 分量描述，如"一碗约300g"
    confidence: float = Field(default=0.8, ge=0, le=1)
    description: str = ""                      # 一句话描述


# ---------- 方案生成 ----------

class PlanMealItem(BaseModel):
    """一餐里的单个菜品"""
    name: str
    portion_g: float = Field(default=100, ge=0)


class PlanMeal(BaseModel):
    """一餐（早/午/晚/加餐）"""
    meal_type: str                             # breakfast / lunch / dinner / snack
    name: str = ""                             # 套餐名，如"低卡版麻婆豆腐套餐"
    items: list[PlanMealItem] = Field(default_factory=list)
    calories: float = 0                        # 本餐热量（落库前若为 0 会由营养库重算）


class PlanExercise(BaseModel):
    """当天的运动安排"""
    type: str                                  # 运动名
    duration_min: int = 30
    calories_burned: int = 0
    tips: str = ""


class PlanDay(BaseModel):
    """方案的某一天"""
    day_index: int                             # 第几天（1 起）
    theme: str = ""                            # 当天主题，如"轻盈开局"
    meals: list[PlanMeal] = Field(default_factory=list)
    exercise: PlanExercise | None = None


class PlanResult(BaseModel):
    """完整方案"""
    summary: str = ""
    daily_target_calories: float = 0
    days: list[PlanDay] = Field(min_length=1)
