"""设备接入相关的请求/响应模型

容错原则：手环/脚本推上来的字段经常缺、类型也不稳（比如 "82" 和 82 混着来），
所以这里的入参一律宽松（Optional + 自动转数字），缺什么就留空，
由路由层决定"能不能算"而不是直接 422 把用户挡在外面。
"""
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator


class DeviceBindIn(BaseModel):
    """绑定设备（浏览器蓝牙连上后登记，或手动添加）"""

    name: str = Field(default="小米手环", max_length=50)
    model: str | None = Field(default=None, max_length=50)
    platform: str = Field(default="xiaomi", max_length=20)
    mac: str | None = Field(default=None, max_length=32)
    ble_name: str | None = Field(default=None, max_length=60)
    battery: int | None = Field(default=None, ge=0, le=100)
    firmware: str | None = Field(default=None, max_length=40)
    bind_source: str = Field(default="ble", max_length=20)


class MetricIn(BaseModel):
    """一条每日指标。字段全可选，缺的不覆盖已有值。"""

    metric_date: str | None = None            # YYYY-MM-DD，不填=今天
    steps: int | None = Field(default=None, ge=0, le=300000)
    distance_km: float | None = Field(default=None, ge=0, le=500)
    calories: float | None = Field(default=None, ge=0, le=20000)
    active_minutes: int | None = Field(default=None, ge=0, le=1440)
    floors: int | None = Field(default=None, ge=0, le=5000)

    hr_avg: int | None = Field(default=None, ge=20, le=250)
    hr_min: int | None = Field(default=None, ge=20, le=250)
    hr_max: int | None = Field(default=None, ge=20, le=250)
    hr_resting: int | None = Field(default=None, ge=20, le=250)

    sleep_minutes: int | None = Field(default=None, ge=0, le=1440)
    deep_sleep_minutes: int | None = Field(default=None, ge=0, le=1440)
    light_sleep_minutes: int | None = Field(default=None, ge=0, le=1440)
    rem_sleep_minutes: int | None = Field(default=None, ge=0, le=1440)
    awake_minutes: int | None = Field(default=None, ge=0, le=1440)

    spo2: float | None = Field(default=None, ge=50, le=100)
    stress: int | None = Field(default=None, ge=0, le=100)

    source: str = Field(default="ble", max_length=20)
    raw: dict[str, Any] | None = None

    @field_validator("metric_date", mode="before")
    @classmethod
    def _date_str(cls, v):
        """容忍 2026/11/04、20261104、带时间戳等写法"""
        if v is None or v == "":
            return None
        if isinstance(v, date):
            return v.isoformat()
        s = str(v).strip().replace("/", "-").replace(".", "-")
        if len(s) >= 10:
            return s[:10]
        if len(s) == 8 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:]}"
        return s


class SyncIn(BaseModel):
    """批量上报：一条或多条（导入文件解析后也走这里）"""

    metrics: list[MetricIn] = Field(default_factory=list)


class ToCheckinIn(BaseModel):
    """把当天手环步数转成一次运动打卡"""

    metric_date: str | None = None
    # 步频/步幅换算出的活动时段，用户可覆盖
    duration_min: int | None = Field(default=None, gt=0, le=600)
    exercise_type: str = Field(default="快走", max_length=30)
