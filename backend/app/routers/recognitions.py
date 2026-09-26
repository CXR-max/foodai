"""食物识别路由：上传图片 → LLM 识别 → 营养库计算营养 → 落库
识别结果可以"保存为饮食记录"（转为 food_records 并得积分）
"""
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.food_record import FoodRecord
from app.models.health_profile import HealthProfile
from app.models.recognition import Recognition
from app.models.user import User
from app.schemas.food_record import SaveRecognitionIn
from app.services import nutrition, points
from app.services.llm.provider import get_llm_provider

router = APIRouter(prefix="/recognitions", tags=["食物识别"])

ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def recognition_dict(r: Recognition) -> dict:
    """识别记录序列化（含完整 result JSON）"""
    return {
        "id": r.id,
        "image_url": f"/uploads/{r.image_path}" if r.image_path else None,
        "provider": r.provider,
        "dish_name": r.dish_name,
        "calories": r.calories,
        "confidence": r.confidence,
        "is_food": r.is_food,
        "result": r.result,
        "food_record_id": r.food_record_id,   # 非 null = 已保存为饮食记录
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.post("")
async def create_recognition(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传食物图片进行识别（前端此请求单独设置 120s 超时）"""
    if (file.content_type or "") not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="仅支持 jpg/png/webp 图片")
    image_bytes = await file.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片不能超过 10MB")

    # 组装用户档案上下文（mock/真模型都会用偏好做过滤，体现"偏好优先"）
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    profile_ctx = {}
    if profile:
        profile_ctx = {
            "dietary_restrictions": profile.dietary_restrictions or [],
            "taste_preferences": profile.taste_preferences or [],
        }

    # 调 LLM 抽象层（内部自带重试与 mock 降级）
    provider = get_llm_provider()
    vision = provider.recognize_food(image_bytes, file.content_type, profile_ctx)

    # 营养库换算（视觉模型只认菜，营养值由本地库算 → 全站口径一致）
    nut = nutrition.match_and_calc(vision.dish_name, vision.portion_g or 0)
    if not vision.is_food:
        nut = {"calories": 0, "protein_g": 0, "fat_g": 0, "carb_g": 0,
               "matched": False, "matched_name": None, "note": "未识别到食物"}

    # 图片存到 uploads/，用 uuid 防中文路径问题
    ext = ALLOWED_TYPES.get(file.content_type, ".jpg")
    image_name = f"{uuid.uuid4().hex}{ext}"
    (settings.UPLOAD_DIR / image_name).write_bytes(image_bytes)

    rec = Recognition(
        user_id=user.id,
        image_path=image_name,
        provider=provider.name,
        result={
            "vision": vision.model_dump(),
            "nutrition": nut,
        },
        dish_name=vision.dish_name,
        calories=nut["calories"],
        confidence=vision.confidence,
        is_food=vision.is_food,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return recognition_dict(rec)


@router.get("")
def list_recognitions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """识别历史（分页）"""
    q = db.query(Recognition).filter(Recognition.user_id == user.id)
    total = q.count()
    items = (q.order_by(Recognition.created_at.desc())
             .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "items": [recognition_dict(r) for r in items]}


@router.post("/{recognition_id}/save")
def save_as_food_record(
    recognition_id: int,
    data: SaveRecognitionIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """把识别结果保存为饮食记录（+2 分）。重复保存返回 409"""
    rec = db.get(Recognition, recognition_id)
    if not rec or rec.user_id != user.id:
        raise HTTPException(status_code=404, detail="识别记录不存在")
    if rec.food_record_id:
        raise HTTPException(status_code=409, detail="该识别结果已保存过饮食记录")
    if not rec.is_food:
        raise HTTPException(status_code=400, detail="未识别到食物，无法保存")

    nut = (rec.result or {}).get("nutrition", {})
    record = FoodRecord(
        user_id=user.id,
        recognition_id=rec.id,
        source="photo",
        dish_name=rec.dish_name or "未知食物",
        ingredients=(rec.result or {}).get("vision", {}).get("ingredients"),
        portion_g=(rec.result or {}).get("vision", {}).get("portion_g") or 0,
        portion_desc=(rec.result or {}).get("vision", {}).get("portion_desc") or "",
        calories=nut.get("calories", 0),
        protein_g=nut.get("protein_g", 0),
        fat_g=nut.get("fat_g", 0),
        carb_g=nut.get("carb_g", 0),
        meal_type=data.meal_type,
        image_path=rec.image_path,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    rec.food_record_id = record.id
    pts = points.award(db, user, "save_recognition",
                       description=f"保存识别记录：{rec.dish_name}", ref_type="food_record", ref_id=record.id)
    db.commit()
    return {"food_record_id": record.id, "points_awarded": pts or 0}
