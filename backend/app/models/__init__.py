"""SQLAlchemy 模型包：统一导入所有模型。

alembic/env.py 里 `import app.models` 依赖本文件把所有表注册到 Base.metadata，
业务代码也从这里或各自模块 import 具体模型。
"""
from app.models.user import User
from app.models.reward import Reward
from app.models.redemption import Redemption
from app.models.points_log import PointsLog
from app.models.health_profile import HealthProfile
from app.models.check_in import CheckIn
from app.models.diet_plan import DietPlan, PlanDay
from app.models.food_record import FoodRecord
from app.models.recognition import Recognition
from app.models.weight_log import WeightLog
from app.models.achievement import Achievement, UserAchievement

__all__ = [
    "User",
    "Reward",
    "Redemption",
    "PointsLog",
    "HealthProfile",
    "CheckIn",
    "DietPlan",
    "PlanDay",
    "FoodRecord",
    "Recognition",
    "WeightLog",
    "Achievement",
    "UserAchievement",
]
