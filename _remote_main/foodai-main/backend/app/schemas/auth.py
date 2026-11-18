"""认证相关请求体"""
from pydantic import BaseModel, Field


class RegisterIn(BaseModel):
    username: str = Field(min_length=2, max_length=50, description="用户名")
    password: str = Field(min_length=6, max_length=64, description="密码（至少6位）")
    nickname: str = Field(default="", max_length=50, description="昵称（可选）")


class LoginIn(BaseModel):
    username: str
    password: str
