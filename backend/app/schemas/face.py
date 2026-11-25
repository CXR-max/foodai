"""人脸登录请求/响应体"""
from pydantic import BaseModel, Field


class FaceStatusIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)


class FaceStatusOut(BaseModel):
    registered: bool
    provider: str
    mock: bool


class FaceEnrollIn(BaseModel):
    image: str = Field(description="data:image/jpeg;base64,... 或纯 base64")


class FaceVerifyIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    images: list[str] = Field(default_factory=list, description="连拍帧（至少 2 张）")
    image: str | None = Field(default=None, description="旧字段：单帧，兼容用")


class FaceVerifyOut(BaseModel):
    ticket: str
    score: float
    expires_in: int


class FaceLoginIn(BaseModel):
    ticket: str
