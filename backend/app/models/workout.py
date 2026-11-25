"""运动记录表（跑步 / 快走 / 骑行 / 跑步机 / 跳绳）

设计要点：
- 一张表装四种来源（source），避免"手环一套、跑步一套、打卡一套"三份数据打架：
    gps    手机定位记录（有轨迹、有配速、有分段）
    band   小米手环导入（有心率、无轨迹）
    manual 手动补录（无轨迹，只记时长/距离）
    course 跟练课程完成（无轨迹）
- 轨迹（track）只有 gps 来源才有；手环只提供心率 —— 两者是互补，不是替代
- 距离 / 配速 / 消耗一律由后端重算，不信任前端提交值（避免"我手机 5.02km 你后台 4.87km"）
- external_id 存手环记录的唯一标识，用于导入幂等（同一份文件重复上传不产生重复记录）
"""
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Integer, String

from app.db.session import Base


class Workout(Base):
    __tablename__ = "workouts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    sport_type = Column(String(20), nullable=False)          # 跑步 / 快走 / 骑行 / 跑步机 / 跳绳
    source = Column(String(10), nullable=False, default="gps")  # gps / band / manual / course

    start_at = Column(DateTime, nullable=False, index=True)
    end_at = Column(DateTime, nullable=True)

    duration_sec = Column(Integer, nullable=False, default=0)   # 有效运动时长（不含暂停）
    distance_m = Column(Float, nullable=True, default=0.0)
    avg_pace_sec_km = Column(Float, nullable=True)              # 平均配速（秒/公里）
    best_pace_sec_km = Column(Float, nullable=True)             # 最快公里配速
    elevation_gain = Column(Float, nullable=True)               # 累计爬升（米）

    calories = Column(Float, nullable=True)
    intensity = Column(String(10), nullable=True)               # light / moderate / intense
    met_used = Column(Float, nullable=True)
    burn_detail = Column(JSON, nullable=True)                   # 消耗计算逐层明细（可解释）

    # ---- 心率：手环能给、GPS 给不了 ----
    avg_hr = Column(Integer, nullable=True)
    max_hr = Column(Integer, nullable=True)
    hr_series = Column(JSON, nullable=True)                     # [{t, bpm}]

    track = Column(JSON, nullable=True)                         # [{lat, lng, t, acc}]，仅 gps
    splits = Column(JSON, nullable=True)                        # [{km, sec, pace_sec_km}]

    external_id = Column(String(64), nullable=True, index=True) # 手环记录唯一 id（幂等）
    # 前端每次运动生成的 UUID，用于保存幂等：网络超时/重复点保存不会产生两条记录
    client_id = Column(String(64), nullable=True, index=True)
    note = Column(String(300), nullable=True)

    # ---- 与现有体系挂钩：方案任务 / 打卡任务 ----
    plan_day_id = Column(Integer, ForeignKey("plan_days.id"), nullable=True)
    checkin_id = Column(Integer, ForeignKey("check_ins.id"), nullable=True)

    created_at = Column(DateTime, nullable=True, default=datetime.now)
