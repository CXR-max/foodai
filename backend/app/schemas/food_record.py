"""饮食记录相关请求体"""
from pydantic import BaseModel, Field


class SaveRecognitionIn(BaseModel):
    """把识别结果保存为饮食记录"""
    meal_type: str = Field(pattern="^(breakfast|lunch|dinner|snack)$")


class CorrectRecognitionIn(BaseModel):
    """手动校准识别结果：只允许改菜名与克数，营养值由营养库重算

    dish_index 为空时表示整条识别记录按单菜处理；多菜识别时传下标只改其中一道。
    """
    dish_name: str = Field(min_length=1, max_length=100)
    portion_g: float = Field(ge=10, le=3000)
    dish_index: int | None = Field(default=None, ge=0, le=9)


class FoodRecordIn(BaseModel):
    """手动录入饮食记录：营养值优先由营养库按菜名+克重自动算，库里没有才需要用户填热量"""
    dish_name: str = Field(min_length=1, max_length=100)
    portion_g: float = Field(default=100, gt=0)
    meal_type: str = Field(pattern="^(breakfast|lunch|dinner|snack)$")
    calories: float | None = Field(default=None, ge=0, description="营养库没收录时手动填")
    protein_g: float | None = None
    fat_g: float | None = None
    carb_g: float | None = None
