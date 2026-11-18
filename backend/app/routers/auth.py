"""认证路由：注册 / 登录 / 当前用户信息"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.deps import get_current_user
from app.core.security import create_token, hash_password, verify_password
from app.db.session import get_db
from app.models.health_profile import HealthProfile
from app.models.user import User
from app.schemas.auth import LoginIn, RegisterIn

router = APIRouter(prefix="/auth", tags=["认证"])


def user_dict(user: User) -> dict:
    """统一的用户信息序列化"""
    return {
        "id": user.id,
        "username": user.username,
        "nickname": user.nickname or user.username,
        "points_total": user.points_total,
    }


@router.post("/register")
def register(data: RegisterIn, request: Request, db: Session = Depends(get_db)):
    """注册（用户名唯一），成功直接返回 token（自动登录）

    同一 IP 5 分钟内最多尝试 5 次，防止批量注册。
    """
    key = ratelimit.client_key(request, "register")
    wait = ratelimit.retry_after(key)
    if wait:
        raise HTTPException(status_code=429, detail=f"注册过于频繁，请 {wait} 秒后再试",
                            headers={"Retry-After": str(wait)})
    ratelimit.record_failure(key)   # 注册按"尝试次数"计数（失败/成功都算一次）

    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(status_code=400, detail="用户名已被注册")
    user = User(
        username=data.username,
        password_hash=hash_password(data.password),
        nickname=data.nickname or data.username,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"token": create_token(user.id), "user": user_dict(user)}


@router.post("/login")
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    """登录，用户名或密码错误统一返回 401（不透露哪个错）

    同一 IP + 用户名 5 分钟内失败 5 次 → 锁 10 分钟（防撞库）。
    """
    key = ratelimit.client_key(request, "login", data.username)
    wait = ratelimit.retry_after(key)
    if wait:
        raise HTTPException(status_code=429, detail=f"密码错误次数过多，请 {wait} 秒后再试",
                            headers={"Retry-After": str(wait)})

    user = db.query(User).filter(User.username == data.username).first()
    if not user or not verify_password(data.password, user.password_hash):
        locked = ratelimit.record_failure(key)
        if locked:
            raise HTTPException(status_code=429, detail=f"密码错误次数过多，请 {locked} 秒后再试",
                                headers={"Retry-After": str(locked)})
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    ratelimit.reset(key)
    return {"token": create_token(user.id), "user": user_dict(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """当前登录信息 + 是否已完善档案（前端据此弹"先去完善档案"引导）"""
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    profile_completed = bool(profile and profile.height_cm and profile.weight_kg and profile.age)
    return {"user": user_dict(user), "profile_completed": profile_completed}
