"""饮食偏离 / 处置 / 当日模式：三张表构成「发现偏离 → 处置 → 回看是否有效」的闭环

为什么必须是三张表而不是一个 JSON 字段：
1. `diet_deviations` 是**事实**：某天某项偏了多少、归因给谁。它只由判定逻辑写入，
   一条偏离只能产生一次（同一天同一项不重复记），后续处置怎么变都不改它。
2. `plan_adjustments` 是**判断**：针对某条偏离做了什么（缩份量/加运动/不处置/重算目标）。
   同一偏离可以有多条（先拒绝再改主意），所以要留 status 与时间。
   处置效果要能在几天后回看（outcome），否则「闭环」只是句口号。
3. `day_modes` 是**例外**：聚餐/训练/生理期/生病/出差。它不改变事实记录，只改变判定口径。
   把例外塞进判定函数会污染口径，塞进用户档案又无法按天生效——按天存才对。

三者分开还有个直接好处：连续 3 天同向偏离可以纯靠 deviations 算出来，
不用反推历史 JSON；而「处置是否有效」靠 adjustments.outcome 对照 deviations。
"""
from datetime import datetime

from sqlalchemy import JSON, Column, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text

from app.db.session import Base


class DietDeviation(Base):
    """一次偏离：某天某项指标越界（事实，不可篡改）"""

    __tablename__ = "diet_deviations"
    __table_args__ = (
        Index("ix_diet_deviations_user_date", "user_id", "dev_date"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    dev_date = Column(Date, nullable=False)
    # over=超上限 under=低于下限；key 是七项之一（calorie/protein/fat/carb/fiber/sodium/sugar）
    kind = Column(String(8), nullable=False)
    nutrient = Column(String(12), nullable=False)
    actual = Column(Float, nullable=True)
    limit_value = Column(Float, nullable=True)   # 越过的那个界（上限或下限）
    delta = Column(Float, nullable=True)         # 超出/不足多少（正数）
    # 归因：plan=方案排错 / extra=方案外加餐 / intake=吃不下没时间 / user=说不清
    attribution = Column(String(10), nullable=True)
    source = Column(String(10), nullable=True)   # plan / extra / total
    severity = Column(String(8), nullable=True)  # soft=浮动带内 / hard=越界
    # 结算时点：midday 半日 / evening 晚餐后 / final 次日定稿
    settlement = Column(String(10), nullable=True)
    # open=待处置 handled=已处置 ignored=用户选择不处置 expired=过窗口自动关闭
    status = Column(String(10), nullable=True, default="open")
    # 例外场景快照（聚餐日超上限不算 hard 等），留档便于解释判定
    day_mode = Column(String(12), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=True, default=datetime.now)
    handled_at = Column(DateTime, nullable=True)


class PlanAdjustment(Base):
    """一次处置：针对某条偏离做了什么，以及事后效果"""

    __tablename__ = "plan_adjustments"
    __table_args__ = (
        Index("ix_plan_adjustments_user_effective", "user_id", "effective_date"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    deviation_id = Column(Integer, ForeignKey("diet_deviations.id"), nullable=True)
    # 处置手段：next_days_portion 后续几天缩份量 / next_days_swap 换菜 /
    # today_exercise 当天加练 / today_supplement 当天补吃 / no_action 不处置 /
    # recalc_target 重算目标（连续偏离时用，不压摄入）
    kind = Column(String(24), nullable=False)
    scope = Column(String(12), nullable=True)    # today / next_days / targets
    payload = Column(JSON, nullable=True)        # 具体参数：缩多少百分比、加多久运动、改了哪几天
    reason = Column(Text, nullable=True)         # 为什么给这个建议（用户看得见的话）
    # proposed=已提议 applied=已执行 declined=用户拒绝 expired=超时失效
    status = Column(String(10), nullable=True, default="applied")
    effective_date = Column(Date, nullable=True)
    # 闭环：几天后回头看，偏差是否收敛（null=还在观察期）
    outcome = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=True, default=datetime.now)
    applied_at = Column(DateTime, nullable=True)


class DayMode(Base):
    """当天模式（例外场景）：不改变事实，只改变判定口径"""

    __tablename__ = "day_modes"
    __table_args__ = (
        Index("ix_day_modes_user_date", "user_id", "mode_date", unique=True),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    mode_date = Column(Date, nullable=False)
    # social 聚餐 / training 额外训练 / period 生理期 / sick 生病 / travel 出差 / normal 正常
    mode = Column(String(12), nullable=False, default="normal")
    note = Column(Text, nullable=True)
    effects = Column(JSON, nullable=True)        # 生效时的口径快照（放宽多少、哪些项不判）
    created_at = Column(DateTime, nullable=True, default=datetime.now)
