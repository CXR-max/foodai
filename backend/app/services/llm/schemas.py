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


class RecognizedDish(BaseModel):
    """多菜场景下的单道菜（一张图可能有饭+菜+汤）"""
    dish_name: str
    category: str = ""
    size_class: str = "normal"
    portion_g: float = Field(default=0, ge=0)
    portion_desc: str = ""
    confidence: float = Field(default=0.8, ge=0, le=1)
    ingredients: list[RecognizedIngredient] = Field(default_factory=list)


class VisionRecognitionResult(BaseModel):
    """视觉模型的识别结果"""
    is_food: bool = True                       # 图片里是否包含食物
    dish_name: str = ""                        # 主菜名（兼容单菜；多菜时取最主要的一道）
    category: str = ""                         # 食材类别（主食/荤菜/素菜/汤粥/早餐/小吃/水果/饮品/其他）
    size_class: str = "normal"                 # 份量档位：small/normal/large（相对常见一份）
    basis: str = ""                            # 份量判断依据（参照物观察，如"普通白瓷碗八分满"）
    ingredients: list[RecognizedIngredient] = Field(default_factory=list)
    portion_g: float = Field(default=0, ge=0)  # 总分量（克）：真模型为原始估算，路由层会用本地标准份校准
    portion_desc: str = ""                     # 分量描述，如"一碗约300g"
    confidence: float = Field(default=0.8, ge=0, le=1)
    description: str = ""                      # 一句话描述
    dishes: list[RecognizedDish] = Field(default_factory=list)  # 多菜：每道菜单独一项


# ---------- 体检报告识别 ----------

class CheckupRawItem(BaseModel):
    """模型抽到的一项指标（name 用报告原文，映射到目录 key 由 checkup_scan 做）"""
    name: str = ""
    value: float | str | None = None   # "<0.5" 这类原始写法原样保留
    unit: str = ""


class CheckupScanResult(BaseModel):
    """体检报告识别结果：只抽报告上真实存在的项，不补全、不估算"""
    is_report: bool = True             # 传错图（自拍/食物）时为 false，前端可提示换图
    report_date: str = ""              # YYYY-MM-DD，报告上没有就留空
    hospital: str = ""
    items: list[CheckupRawItem] = Field(default_factory=list)


# ---------- 方案生成 ----------

class PlanMealItem(BaseModel):
    """一餐里的单个菜品"""
    name: str
    portion_g: float = Field(default=100, ge=0)
    ingredients: list[str] = Field(default_factory=list)  # 主要食材，供购物清单聚合


class PlanMeal(BaseModel):
    """一餐（早/午/晚/加餐）"""
    meal_type: str                             # breakfast / lunch / dinner / snack
    name: str = ""                             # 套餐名，如"低卡版麻婆豆腐套餐"
    items: list[PlanMealItem] = Field(default_factory=list)
    calories: float = 0                        # 本餐热量（落库前若为 0 会由营养库重算）


class PlanExercise(BaseModel):
    """当天的运动安排（一天可以有多项：主项 + 拉伸）"""
    type: str                                  # 运动名
    duration_min: int = 30
    calories_burned: int = 0
    tips: str = ""
    intensity: str = "moderate"                # light / moderate / intense


class PlanDay(BaseModel):
    """方案的某一天"""
    day_index: int                             # 第几天（1 起）
    theme: str = ""                            # 当天主题，如"轻盈开局"
    meals: list[PlanMeal] = Field(default_factory=list)
    exercise: PlanExercise | None = None       # 主项（兼容旧前端/旧方案）
    exercises: list[PlanExercise] = Field(default_factory=list)   # 完整运动处方
    sleep: dict | None = None                  # 睡眠方案（作息/时长/建议）
    targets: dict | None = None                # 当日目标（热量上下限/饮水/睡眠/运动消耗）
    water_ml: int | None = None                # 当日饮水目标
    nutrition: dict | None = None              # 这天餐食实排的七项营养（排完即存，不再重算）
    day_flags: dict | None = None               # 生成留痕：降级项 / 越界项 / 数据缺失项


class PlanResult(BaseModel):
    """完整方案"""
    summary: str = ""
    daily_target_calories: float = 0
    days: list[PlanDay] = Field(min_length=1)
    targets: dict | None = None                # 计划级每日目标（热量区间/蛋白/饮水/睡眠/运动）
