"""饮食记录表"""
from datetime import datetime

from sqlalchemy import JSON, Column, Date, DateTime, Float, ForeignKey, Index, Integer, String

from app.db.session import Base


class FoodRecord(Base):
    __tablename__ = "food_records"
    # 统计/趋势全是「某用户某天/某区间」查询，复合索引比两个单列索引更有效
    __table_args__ = (Index("ix_food_records_user_eaten_date", "user_id", "eaten_date"), )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    recognition_id = Column(Integer, ForeignKey("recognitions.id"), nullable=True)
    source = Column(String(10), nullable=True)  # recognition / manual（怎么记的）
    # 归属：plan=按方案吃的（含拍照确认属于某餐）、extra=方案外的加餐、unknown=分不清
    # 判定要「看总量、看归属」：方案内越界是方案排错了，方案外越界是用户自己的选择
    origin = Column(String(10), nullable=True)
    dish_name = Column(String(100), nullable=False)
    ingredients = Column(JSON, nullable=True)
    portion_g = Column(Float, nullable=True)
    portion_desc = Column(String(50), nullable=True)
    calories = Column(Float, nullable=True)
    protein_g = Column(Float, nullable=True)
    fat_g = Column(Float, nullable=True)
    carb_g = Column(Float, nullable=True)
    # 结构项：方案七项判定要用；手动/拍照识别暂未采集时留空（按 0 计，不猜）
    fiber_g = Column(Float, nullable=True)
    sodium_mg = Column(Float, nullable=True)
    sugar_g = Column(Float, nullable=True)
    meal_type = Column(String(10), nullable=True)  # breakfast/lunch/dinner/snack
    eaten_at = Column(DateTime, nullable=True)
    eaten_date = Column(Date, nullable=True, index=True)
    image_path = Column(String(300), nullable=True)
    created_at = Column(DateTime, nullable=True, default=datetime.now)

    # ---- 方案内打卡的份量留痕 ----
    # 「菜品对、份量不对」必须能和「方案排多了」分开：前者可以让你下一餐紧一点，
    # 后者只能由系统改方案。没有这三列，份量吃多会被归因成方案错，
    # 结果就是「用户多吃了，系统道歉」，处置方向整个反过来。
    plan_portion_g = Column(Float, nullable=True)      # 方案这道菜排的克重
    portion_delta_g = Column(Float, nullable=True)     # 实际 − 方案（正=吃多了）
    portion_source = Column(String(12), nullable=True)  # photo / photo_corrected / plan

    # ---- 作废（一餐一账）----
    # 同一餐拍两次照、或拍照顶掉手动记录时，旧记录打作废标记而不是物理删除：
    # 历史要能回看，识别记录还挂着 food_record_id 做溯源。
    voided_at = Column(DateTime, nullable=True)
    voided_reason = Column(String(40), nullable=True)
