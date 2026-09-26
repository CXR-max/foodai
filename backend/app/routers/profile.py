"""健康档案路由：档案读写（自动算 BMI/BMR/热量目标）+ 体重记录"""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.health_profile import HealthProfile
from app.models.user import User
from app.models.weight_log import WeightLog
from app.schemas.profile import ProfileIn, WeightIn
from app.services import nutrition, points

router = APIRouter(prefix="/profile", tags=["健康档案"])


def profile_dict(p: HealthProfile, user: User) -> dict:
    """档案序列化 + 实时计算的身体指标"""
    bmi = nutrition.calc_bmi(p.weight_kg, p.height_cm)
    bmr = nutrition.calc_bmr(p.weight_kg, p.height_cm, p.age, p.gender)
    return {
        "height_cm": p.height_cm, "weight_kg": p.weight_kg,
        "age": p.age, "gender": p.gender,
        "activity_level": p.activity_level, "goal": p.goal,
        "target_weight_kg": p.target_weight_kg,
        "daily_calorie_target": p.daily_calorie_target,
        "taste_preferences": p.taste_preferences or [],
        "dietary_restrictions": p.dietary_restrictions or [],
        "exercise_preferences": p.exercise_preferences or [],
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        # ---- 计算指标 ----
        "bmi": bmi,
        "bmi_level": nutrition.bmi_level(bmi),
        "bmr": bmr,
        "nickname": user.nickname or user.username,
    }


@router.get("")
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取档案；没填过返回空壳（前端据此显示引导）"""
    p = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    if not p:
        return {
            "height_cm": None, "weight_kg": None, "age": None, "gender": None,
            "activity_level": "moderate", "goal": "maintain", "target_weight_kg": None,
            "daily_calorie_target": None,
            "taste_preferences": [], "dietary_restrictions": [], "exercise_preferences": [],
            "updated_at": None, "bmi": None, "bmi_level": "未知", "bmr": None,
            "nickname": user.nickname or user.username, "profile_completed": False,
        }
    result = profile_dict(p, user)
    result["profile_completed"] = bool(p.height_cm and p.weight_kg and p.age)
    return result


@router.put("")
def save_profile(data: ProfileIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """保存档案（upsert）。服务端计算 BMI/BMR/每日热量目标；首次完善奖励 20 分"""
    p = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    if not p:
        p = HealthProfile(user_id=user.id)
        db.add(p)
    # 只更新客户端传来的字段（支持分次填写）
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(p, field, value)
    # 服务端统一计算每日热量目标（不信任前端算的）
    p.daily_calorie_target = nutrition.calc_daily_target(
        p.weight_kg, p.height_cm, p.age, p.gender, p.activity_level or "moderate", p.goal or "maintain",
    )
    db.commit()
    db.refresh(p)

    # 首次把档案填完整（身高+体重+年龄）→ 奖励 20 分（仅一次，防刷在 award 内部）
    if p.height_cm and p.weight_kg and p.age:
        pts = points.award(db, user, "complete_profile", once_ever=True)
        if pts:
            db.commit()

    result = profile_dict(p, user)
    result["profile_completed"] = True
    result["points_awarded"] = pts if (p.height_cm and p.weight_kg and p.age) else None
    return result


@router.post("/weights")
def add_weight(data: WeightIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """记体重：写轨迹 + 回写档案当前体重；每天首次记录 +2 分"""
    record_date = date.fromisoformat(data.recorded_date) if data.recorded_date else date.today()
    log = WeightLog(user_id=user.id, weight_kg=data.weight_kg, recorded_date=record_date, note=data.note)
    db.add(log)
    # 同步更新档案里的当前体重
    p = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    if p:
        p.weight_kg = data.weight_kg
        p.daily_calorie_target = nutrition.calc_daily_target(
            p.weight_kg, p.height_cm, p.age, p.gender, p.activity_level or "moderate", p.goal or "maintain",
        )
    pts = points.award(db, user, "log_weight", once_per_day=True,
                       description=f"记录体重 {data.weight_kg}kg")
    db.commit()
    return {"id": log.id, "weight_kg": log.weight_kg, "recorded_date": str(record_date), "points_awarded": pts or 0}


@router.get("/weights")
def list_weights(days: int = Query(default=90, ge=1, le=365),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """体重轨迹（升序，画折线图用）"""
    start = date.today().toordinal() - days
    logs = (db.query(WeightLog)
            .filter(WeightLog.user_id == user.id, WeightLog.recorded_date >= date.fromordinal(start))
            .order_by(WeightLog.recorded_date.asc()).all())
    return [{"weight_kg": w.weight_kg, "recorded_date": str(w.recorded_date)} for w in logs]
