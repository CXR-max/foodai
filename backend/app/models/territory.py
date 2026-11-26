"""领地跑：格子、每次结算、用户偏好、事件

设计取舍（见 docs/运动板块-领地跑玩法与实现方案.md）：
- 一格一个 owner，天然不重叠；前端把同色格合并成轮廓，用户看不到网格。
- 活力 0–100；跑过自己的格 +20，被抢 -45（防守 27，夺回 90）。
- 只认 GPS + 跑步/快走；骑行/手动/手环/课程不进领地。
"""
from datetime import datetime

from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, Float, ForeignKey,
                        Integer, String, UniqueConstraint)
from sqlalchemy.orm import relationship

from app.db.session import Base


class TerritoryCell(Base):
    __tablename__ = "territory_cells"

    id = Column(Integer, primary_key=True)
    cell_key = Column(String(40), nullable=False, unique=True, index=True)
    ix = Column(Integer, nullable=False, index=True)
    iy = Column(Integer, nullable=False, index=True)

    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    prev_owner_id = Column(Integer, nullable=True)          # 上一任，用于 48h 夺回加成
    vitality = Column(Float, nullable=False, default=0.0)
    state = Column(String(10), nullable=False, default="neutral")  # neutral / owned

    first_claimed_at = Column(DateTime, nullable=True)
    last_visit_at = Column(DateTime, nullable=True)
    last_decay_date = Column(Date, nullable=True)
    stolen_at = Column(DateTime, nullable=True)
    owner_changed_at = Column(DateTime, nullable=True, index=True)
    shield_until = Column(DateTime, nullable=True)
    landmark_id = Column(Integer, nullable=True)

    owner = relationship("User", foreign_keys=[owner_id])


class TerritoryRun(Base):
    __tablename__ = "territory_runs"

    id = Column(Integer, primary_key=True)
    workout_id = Column(Integer, ForeignKey("workouts.id"), nullable=False, unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    valid = Column(Boolean, nullable=False, default=False)
    reason = Column(String(120), nullable=True)
    cells_new = Column(Integer, nullable=False, default=0)
    cells_filled = Column(Integer, nullable=False, default=0)
    cells_attacked = Column(Integer, nullable=False, default=0)
    cells_stolen = Column(Integer, nullable=False, default=0)
    cells_repaired = Column(Integer, nullable=False, default=0)
    cells_cut = Column(Integer, nullable=False, default=0)
    loop = Column(Boolean, nullable=False, default=False)
    cells = Column(JSON, nullable=True)                     # 本次结算的格子 key，前端做圈地动画
    created_at = Column(DateTime, nullable=True, default=datetime.now)


class TerritoryPref(Base):
    __tablename__ = "territory_prefs"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    anonymous = Column(Boolean, nullable=False, default=False)
    color = Column(String(16), nullable=True)              # 演示/自定义颜色
    home_lat = Column(Float, nullable=True)
    home_lng = Column(Float, nullable=True)
    home_radius_m = Column(Integer, nullable=False, default=300)
    updated_at = Column(DateTime, nullable=True, default=datetime.now)


class TerritoryEvent(Base):
    __tablename__ = "territory_events"

    id = Column(Integer, primary_key=True)
    type = Column(String(20), nullable=False, index=True)   # claim / fill / steal / recapture / decay
    cell_key = Column(String(40), nullable=False, index=True)
    actor_id = Column(Integer, nullable=True, index=True)
    victim_id = Column(Integer, nullable=True, index=True)
    run_id = Column(Integer, nullable=True)
    meta = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=True, default=datetime.now, index=True)


class TerritoryChallenge(Base):
    """下战书：一次 24 小时的异步约战。

    跑步没法实时对战，竞争感只能靠「期限」制造：
    挑战者在详情页下战书，24 小时内只要真的切走对方任意一块地就算赢；
    到期未得手算对方守住了。结算只记战绩，不动格子，避免破坏结算主逻辑。
    """
    __tablename__ = "territory_challenges"

    id = Column(Integer, primary_key=True)
    challenger_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    target_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(10), nullable=False, default="pending")   # pending / win / lose
    snapshot_area = Column(Float, nullable=False, default=0.0)       # 下战书时对方面积
    created_at = Column(DateTime, nullable=True, default=datetime.now)
    expires_at = Column(DateTime, nullable=True, index=True)
    accepted_at = Column(DateTime, nullable=True)                    # 对方点过「应战」
    resolved_at = Column(DateTime, nullable=True)


class UserFriend(Base):
    """单向关注即可：A 加 B 后，A 能在「好友」筛选里看到 B 的地。

    不做好友申请/同意，避免为了一个筛选先做一套消息系统；
    双方互加就是互相可见。对校园小范围够用。
    """
    __tablename__ = "user_friends"
    __table_args__ = (UniqueConstraint("user_id", "friend_id", name="uq_user_friend"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    friend_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=True, default=datetime.now)
