"""
模型包：集中导入所有表模型

- Alembic 迁移靠这个文件发现全部表（alembic/env.py 里 `import app.models`）
- 新增模型后记得在这里补一行导入 + 加入 __all__

整合说明：
- 保留 PlanChatMessage（plan_chat 计划对话模型）
- 保留 Achievement / UserAchievement（成就徽章体系）
- 营养库 7 张表（foods / food_aliases / food_relations / food_ingredients /
  food_recipes / food_recipe_items / import_batches）在本分支补齐
"""
from app.models.user import User
from app.models.health_profile import HealthProfile
from app.models.recognition import Recognition
from app.models.food_record import FoodRecord
from app.models.diet_plan import DietPlan, PlanDay
from app.models.diet_deviation import DayMode, DietDeviation, PlanAdjustment
from app.models.plan_chat import PlanChatMessage
from app.models.check_in import CheckIn
from app.models.workout import Workout
from app.models.user_face import UserFace
from app.models.points_log import PointsLog
from app.models.weight_log import WeightLog
from app.models.reward import Reward
from app.models.redemption import Redemption
from app.models.achievement import Achievement, UserAchievement
from app.models.weekly_report import WeeklyReportCache
from app.models.sleep_log import SleepLog
from app.models.water_log import WaterLog
from app.models.food import (
    Food,
    FoodAlias,
    FoodIngredient,
    FoodRecipe,
    FoodRecipeItem,
    FoodRelation,
    ImportBatch,
)

__all__ = [
    "User", "HealthProfile", "Recognition", "FoodRecord",
    "DietPlan", "PlanDay", "CheckIn", "Workout", "PointsLog",
    "DietDeviation", "PlanAdjustment", "DayMode",
    "WeightLog", "Reward", "Redemption", "PlanChatMessage",
    "Achievement", "UserAchievement", "UserFace",
    "WeeklyReportCache",
    "SleepLog", "WaterLog",
    "Food", "FoodAlias", "FoodRelation", "FoodIngredient",
    "FoodRecipe", "FoodRecipeItem", "ImportBatch",
]
