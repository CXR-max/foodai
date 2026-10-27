"""
模型包：集中导入所有表模型

- Alembic 迁移靠这个文件发现全部表（alembic/env.py 里 `import app.models`）
- 新增模型后记得在这里补一行导入 + 加入 __all__
"""
from app.models.user import User
from app.models.health_profile import HealthProfile
from app.models.recognition import Recognition
from app.models.food_record import FoodRecord
from app.models.diet_plan import DietPlan, PlanDay
from app.models.plan_chat import PlanChatMessage
from app.models.check_in import CheckIn
from app.models.points_log import PointsLog
from app.models.weight_log import WeightLog
from app.models.reward import Reward
from app.models.redemption import Redemption

__all__ = [
    "User", "HealthProfile", "Recognition", "FoodRecord",
    "DietPlan", "PlanDay", "CheckIn", "PointsLog",
    "WeightLog", "Reward", "Redemption", "PlanChatMessage",
]
