"""运动记录请求体 / 返回体"""
from datetime import datetime

from pydantic import BaseModel, Field


class TrackPointIn(BaseModel):
    """单个轨迹点。t = 有效运动秒数（暂停不递增），acc = 定位精度（米）"""
    lat: float
    lng: float
    t: float = 0
    acc: float | None = None


class WorkoutIn(BaseModel):
    """上传一次运动记录（结束后一次性提交，不做实时上传）"""
    sport_type: str = Field(default="跑步", max_length=20)
    start_at: datetime
    end_at: datetime | None = None
    duration_sec: int = Field(default=0, ge=0, le=86400)
    track: list[TrackPointIn] = Field(default_factory=list)
    # 前端算出来的距离只作对照，后端以轨迹重算为准（无轨迹时如手动补录才采信）
    distance_m: float | None = Field(default=None, ge=0)
    # 手环/第三方导入时可能带来心率
    avg_hr: int | None = Field(default=None, ge=0, le=260)
    max_hr: int | None = Field(default=None, ge=0, le=260)
    hr_series: list[dict] | None = None
    note: str = Field(default="", max_length=300)
    plan_day_id: int | None = None
    source: str = Field(default="gps", pattern="^(gps|band|manual|course)$")
    # 保存幂等键（前端每次开始运动生成一个 UUID）
    client_id: str | None = Field(default=None, max_length=64)


class ManualWorkoutIn(BaseModel):
    """无 GPS 的手动补录（跑步机 / 跳绳 / 忘带手机）"""
    sport_type: str = Field(default="跑步机", max_length=20)
    start_at: datetime | None = None
    duration_sec: int = Field(ge=60, le=86400)
    distance_m: float | None = Field(default=None, ge=0)
    # 用户自选强度；不传则按运动类型 + 时速自动估算
    intensity: str | None = Field(default=None, pattern="^(light|moderate|intense)$")
    note: str = Field(default="", max_length=300)
    # 按方案处方补录时关联方案天，便于后续算依从性
    plan_day_id: int | None = None
    client_id: str | None = Field(default=None, max_length=64)
