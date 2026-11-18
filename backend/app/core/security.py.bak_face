"""
安全工具：密码哈希（pbkdf2）+ JWT 签发/校验
- 不使用 passlib/bcrypt（Windows 下有兼容坑），用标准库 hashlib.pbkdf2_hmac 自实现
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings

_ITERATIONS = 200_000  # pbkdf2 迭代次数


def hash_password(password: str) -> str:
    """明文密码 → 哈希字符串，格式：pbkdf2$迭代次数$salt十六进制$哈希十六进制"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS).hex()
    return f"pbkdf2${_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """校验明文密码与存储的哈希是否匹配"""
    try:
        _, iterations, salt, digest = stored.split("$")
        calc = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        ).hex()
        return secrets.compare_digest(calc, digest)
    except Exception:
        return False


def create_token(user_id: int) -> str:
    """为用户签发 JWT（有效期见配置 JWT_EXPIRE_DAYS）"""
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(days=settings.JWT_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> int | None:
    """解析 JWT，返回 user_id；无效/过期返回 None"""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        return int(payload["sub"])
    except Exception:
        return None
