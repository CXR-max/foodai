"""智能穿戴设备接入：设备登记表 + 每日指标表

设计要点
- 一个用户可绑定多台设备（手环 / 手表 / 手机计步）
- 指标按「用户 + 设备 + 日期」唯一，重复同步是覆盖更新（幂等），不会插重复行
- `source` 记录数据从哪来：ble=浏览器蓝牙直连、import=导出文件导入、
  api=第三方脚本推送、manual=手动填、demo=演示数据
- `raw` 保留原始报文，方便排查"某个字段为什么是 0"
"""
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String,
    UniqueConstraint,
)

from app.db.session import Base


class Device(Base):
    """已绑定的穿戴设备"""

    __tablename__ = "devices"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    name = Column(String(50), nullable=False)            # 显示名，如「小米手环 8」
    model = Column(String(50), nullable=True)            # 型号，如 Mi Band 8
    platform = Column(String(20), nullable=True)         # xiaomi / apple / huawei / other
    mac = Column(String(32), nullable=True)              # BLE MAC（可选）
    ble_name = Column(String(60), nullable=True)         # 蓝牙广播名（可选）

    battery = Column(Integer, nullable=True)             # 电量 %
    firmware = Column(String(40), nullable=True)         # 固件版本
    status = Column(String(20), nullable=False, default="bound")  # bound / unsupported / offline
    bind_source = Column(String(20), nullable=False, default="ble")  # ble / manual / import

    last_sync_at = Column(DateTime, nullable=True)
    last_sync_source = Column(String(20), nullable=True)
    # 后台是否自动往这台设备刷「实时」数据（页面上的自动同步开关）
    auto_sync = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=True, default=datetime.now)


class DeviceMetric(Base):
    """设备上报的每日健康指标（一条 = 某设备某天）"""

    __tablename__ = "device_metrics"
    __table_args__ = (
        UniqueConstraint("user_id", "device_id", "metric_date", name="uq_device_metric_day"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    metric_date = Column(Date, nullable=False, index=True)

    # ---- 活动 ----
    steps = Column(Integer, nullable=True)
    distance_km = Column(Float, nullable=True)
    calories = Column(Float, nullable=True)          # 手环记录的活动消耗 kcal
    active_minutes = Column(Integer, nullable=True)  # 中高强度活动时长
    floors = Column(Integer, nullable=True)          # 爬楼层数

    # ---- 心率 ----
    hr_avg = Column(Integer, nullable=True)
    hr_min = Column(Integer, nullable=True)
    hr_max = Column(Integer, nullable=True)
    hr_resting = Column(Integer, nullable=True)

    # ---- 睡眠（分钟）----
    sleep_minutes = Column(Integer, nullable=True)
    deep_sleep_minutes = Column(Integer, nullable=True)
    light_sleep_minutes = Column(Integer, nullable=True)
    rem_sleep_minutes = Column(Integer, nullable=True)
    awake_minutes = Column(Integer, nullable=True)

    # ---- 其他 ----
    spo2 = Column(Float, nullable=True)              # 血氧 %
    stress = Column(Integer, nullable=True)          # 压力 0-100

    source = Column(String(20), nullable=False, default="ble")
    raw = Column(JSON, nullable=True)

    created_at = Column(DateTime, nullable=True, default=datetime.now)
    updated_at = Column(DateTime, nullable=True, default=datetime.now, onupdate=datetime.now)


class DeviceSample(Base):
    """实时样本（分钟级快照），给「实时」曲线用

    和 DeviceMetric 的分工：
    - DeviceMetric 是「一天一条」的汇总，页面上的今日数据/趋势图读它
    - DeviceSample 是「一分钟一条」的瞬时值，只用于今日走势与"刚刚更新"的感觉
    保留 3 天，后台定时清理，不会把库撑大。
    """

    __tablename__ = "device_samples"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    sampled_at = Column(DateTime, nullable=False, index=True)

    steps = Column(Integer, nullable=True)       # 当日累计步数
    hr = Column(Integer, nullable=True)          # 瞬时心率
    calories = Column(Float, nullable=True)      # 当日累计活动消耗
    source = Column(String(20), nullable=False, default="auto")  # auto=后台同步 / demo=模拟
    created_at = Column(DateTime, nullable=True, default=datetime.now)
