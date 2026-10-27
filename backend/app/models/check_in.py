"""打卡表：日历上的任务与完成记录，两种来源：

1. 从方案"导入日历"生成的任务 → status=pending，逐项完成后变 done 并得积分
   （按完成度给分：单项 +3，当日全清 +10）
2. 手动饮食/运动打卡 → 直接 done
"""
from datetime import date, datetime

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, String

from app.db.session import Base


class CheckIn(Base):
    __tablename__ = "check_ins"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    check_date = Column(Date, default=date.today, index=True)
    check_type = Column(String(10), nullable=False)   # diet=饮食 / exercise=运动
    task_key = Column(String(30), default="manual")   # 任务标识：meal_breakfast/meal_lunch/meal_dinner/meal_snack/exercise/manual_food/manual_exercise

    status = Column(String(10), default="done")       # pending=待完成 / done=已完成
    points_awarded = Column(Integer, default=0)       # 本次完成实得积分

    # ---- 关联（按来源二选一）----
    food_record_id = Column(Integer, ForeignKey("food_records.id"))  # 饮食打卡关联的记录
    plan_day_id = Column(Integer, ForeignKey("plan_days.id"))        # 从方案哪一天导入

    # ---- 运动打卡字段 ----
    exercise_type = Column(String(30))
    duration_min = Column(Integer)
    calories_burned = Column(Float)
    note = Column(String(300))

    created_at = Column(DateTime, default=datetime.now)
