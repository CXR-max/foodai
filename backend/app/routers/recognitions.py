"""食物识别路由：上传图片 → LLM 识别 → 营养库计算营养 → 落库
识别结果可以"保存为饮食记录"（转为 food_records 并得积分）；
历史记录支持分页 / 删除 / 重新识别。
"""
import hashlib
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.food_record import FoodRecord
from app.models.health_profile import HealthProfile
from app.models.recognition import Recognition
from app.models.user import User
from app.schemas.food_record import CorrectRecognitionIn, SaveRecognitionIn
from app.services import nutrition, points, recognition
from app.services.achievements import unlock_all
from app.services.images import compress_for_vision, validate_image
from app.services.llm import foods_data
from app.services.llm.provider import get_llm_provider
from app.services.llm.schemas import RecognizedDish

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


def _dish_views(vision):
    """把识别结果统一成「每道菜一项」；单菜模型没给 dishes 时用顶层字段兜底"""
    if vision.dishes:
        return list(vision.dishes)
    return [RecognizedDish(
        dish_name=vision.dish_name,
        category=vision.category,
        size_class=vision.size_class,
        portion_g=vision.portion_g,
        portion_desc=vision.portion_desc,
        confidence=vision.confidence,
        ingredients=vision.ingredients,
    )]


class _DishView:
    """给 recognition.evaluate 用的轻量视图（它只读 dish_name/confidence/is_food）"""

    def __init__(self, dish: RecognizedDish):
        self.dish_name = dish.dish_name
        self.confidence = dish.confidence
        self.is_food = True


def _totals(dishes: list[dict]) -> dict:
    """多道菜的营养合计（一份照片一行展示，统计仍按 food_records 逐菜算）"""
    keys = ("calories", "protein_g", "fat_g", "carb_g")
    total = {k: round(sum(float((d.get("nutrition") or {}).get(k) or 0) for d in dishes), 1) for k in keys}
    matched = bool(dishes) and all((d.get("nutrition") or {}).get("matched") for d in dishes)
    primary = dishes[0]["nutrition"] if dishes else {}
    total.update({
        "matched": matched,
        "matched_name": primary.get("matched_name"),
        "match_method": primary.get("match_method", "none"),
        "match_score": primary.get("match_score", 0.0),
        "nutrition_source": "dish_library" if matched else "mixed",
        "note": f"共 {len(dishes)} 道菜，按本地营养库合计" if dishes else "未识别到食物",
    })
    return total


def _analyze_image(image_bytes: bytes, content_type: str, profile_ctx: dict):
    """调视觉模型 → 逐道菜克重校准 + 营养库换算 + 匹配判定。

    返回 (vision, dishes, totals, raw_portion_g, provider)
    dishes: [{dish_name, category, size_class, portion_g, confidence, ingredients, nutrition, match}]
    """
    provider = get_llm_provider()
    vision = provider.recognize_food(image_bytes, content_type, profile_ctx)

    dishes: list[dict] = []
    if vision.is_food:
        for item in _dish_views(vision):
            raw = item.portion_g or 0
            item.portion_g = nutrition.resolve_portion(item.dish_name, raw, item.size_class)
            nut = nutrition.match_and_calc(item.dish_name, item.portion_g, item.category)
            match_info = recognition.evaluate(_DishView(item), nut)
            dishes.append({
                **item.model_dump(),
                "portion_g_raw": raw,
                "nutrition": nut,
                "match": match_info,
            })
        # 顶层字段与主菜保持一致，兼容旧的单菜逻辑/前端
        if dishes:
            vision.dish_name = dishes[0]["dish_name"]
            vision.portion_g = dishes[0]["portion_g"]
            vision.ingredients = _dish_views(vision)[0].ingredients

    totals = _totals(dishes)
    if not vision.is_food:
        totals = {"calories": 0, "protein_g": 0, "fat_g": 0, "carb_g": 0,
                  "matched": False, "matched_name": None, "match_method": "none", "match_score": 0.0,
                  "nutrition_source": "none", "note": "未识别到食物"}
    return vision, dishes, totals, (dishes[0]["portion_g_raw"] if dishes else (vision.portion_g or 0)), provider


def _build_profile_ctx(db: Session, user_id: int) -> dict:
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user_id).first()
    if not profile:
        return {}
    return {
        "dietary_restrictions": profile.dietary_restrictions or [],
        "taste_preferences": profile.taste_preferences or [],
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

    # 真解码校验：坏图/伪造 MIME/超大分辨率不送模型，直接 400（省一次视觉调用）
    try:
        validate_image(image_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 防刷 1：每日识别配额
    quota = settings.VISION_DAILY_QUOTA
    if quota > 0:
        since = datetime.now() - timedelta(days=1)
        used = (db.query(Recognition)
                .filter(Recognition.user_id == user.id, Recognition.created_at >= since)
                .count())
        if used >= quota:
            raise HTTPException(status_code=429,
                                detail=f"今天已识别 {used} 次，达到每日上限 {quota} 次，明天再来")

    # 防刷 2：同图去重（窗口内重复上传直接回放上次结果，不再烧一次视觉调用）
    digest = hashlib.sha256(image_bytes).hexdigest()
    if settings.VISION_DEDUPE_HOURS > 0:
        window = datetime.now() - timedelta(hours=settings.VISION_DEDUPE_HOURS)
        recent = (db.query(Recognition)
                  .filter(Recognition.user_id == user.id, Recognition.created_at >= window)
                  .order_by(Recognition.created_at.desc()).limit(50).all())
        for old in recent:
            if (old.result or {}).get("image_sha256") == digest:
                return {**recognition_dict(old), "replayed": True}

    # 压缩瘦身：长边 ≤1024 + JPEG，显著减少视觉 token 与上传耗时（失败自动回退原图）
    image_bytes, content_type = compress_for_vision(image_bytes, file.content_type or "image/jpeg")

    profile_ctx = _build_profile_ctx(db, user.id)
    # 视觉调用是同步阻塞的：丢到线程池执行，避免一次识别把整个事件循环卡住（其他人全排队）
    vision, dishes, nut, raw_portion_g, provider = await run_in_threadpool(
        _analyze_image, image_bytes, content_type, profile_ctx
    )

    # 图片存到 uploads/，用 uuid 防中文路径问题（存压缩后的版本，省空间）
    ext = ALLOWED_TYPES.get(content_type, ".jpg")
    image_name = f"{uuid.uuid4().hex}{ext}"
    (settings.UPLOAD_DIR / image_name).write_bytes(image_bytes)

    rec = Recognition(
        user_id=user.id,
        image_path=image_name,
        provider=provider.active_name,   # 兜底到 mock 时如实标注
        result={
            "vision": {**vision.model_dump(exclude={"dishes"}), "dishes": dishes, "portion_g_raw": raw_portion_g},
            "nutrition": nut,
            "match": dishes[0]["match"] if dishes else recognition.evaluate(vision, nut),
            "dishes": dishes,                # 每道菜：克重/营养/匹配/是否需要确认
            "image_sha256": digest,          # 同图去重用（原图哈希）
        },
        dish_name=vision.dish_name,
        calories=nut["calories"],
        confidence=vision.confidence,
        is_food=vision.is_food,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    unlock_all(db, user)   # 累计识别次数成就
    db.commit()
    return recognition_dict(rec)


@router.get("")
def list_recognitions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """识别历史（分页，一页默认 10 条）"""
    q = db.query(Recognition).filter(Recognition.user_id == user.id)
    total = q.count()
    items = (q.order_by(Recognition.created_at.desc())
             .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "page": page, "page_size": page_size,
            "items": [recognition_dict(r) for r in items]}


@router.patch("/{recognition_id}")
def correct_recognition(
    recognition_id: int,
    data: CorrectRecognitionIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """用户手动校准：只改菜名+克数，营养值由本地库重算

    - 首次校准保留模型原始输出快照（result.vision.original，可当评测数据）
    - 已保存为饮食记录时同步更新记录，保证统计口径一致
    """
    rec = db.get(Recognition, recognition_id)
    if not rec or rec.user_id != user.id:
        raise HTTPException(status_code=404, detail="识别记录不存在")

    result = dict(rec.result or {})
    dishes = list(result.get("dishes") or [])

    # ---- 多菜场景：只校准其中一道 ----
    if data.dish_index is not None and 0 <= data.dish_index < len(dishes):
        nut = nutrition.match_and_calc(data.dish_name, data.portion_g)
        item = dict(dishes[data.dish_index])
        view = _DishView(RecognizedDish(
            dish_name=data.dish_name, category=item.get("category", ""),
            portion_g=data.portion_g, confidence=1.0,
        ))
        match_info = recognition.evaluate(view, nut)
        match_info.update({"needs_confirm": False, "reason_codes": [], "reasons": [], "corrected": True})
        entry = foods_data.find_food(data.dish_name)
        names = entry.get("ingredients") if entry else [i.get("name") for i in (item.get("ingredients") or [])]
        item.update({
            "dish_name": data.dish_name,
            "portion_g": data.portion_g,
            "portion_desc": f"手动校准：{data.dish_name} {data.portion_g:g}g"[:50],
            "ingredients": [{"name": name, "portion_g": 0} for name in (names or [])],
            "nutrition": nut,
            "match": match_info,
            "corrected": True,
        })
        dishes[data.dish_index] = item
        totals = _totals(dishes)
        result["dishes"] = dishes
        result["nutrition"] = totals
        result["match"] = dishes[0]["match"] if dishes else match_info
        rec.result = result
        rec.dish_name = dishes[0]["dish_name"]
        rec.calories = totals["calories"]
        rec.is_food = True

        ids = list(result.get("food_record_ids") or [])
        if data.dish_index < len(ids):
            record = db.get(FoodRecord, ids[data.dish_index])
            if record:
                record.dish_name = data.dish_name
                record.portion_g = data.portion_g
                record.portion_desc = item["portion_desc"]
                record.ingredients = item["ingredients"]
                record.calories = nut["calories"]
                record.protein_g = nut["protein_g"]
                record.fat_g = nut["fat_g"]
                record.carb_g = nut["carb_g"]
        db.commit()
        db.refresh(rec)
        return recognition_dict(rec)

    nut = nutrition.match_and_calc(data.dish_name, data.portion_g)

    vision = dict(result.get("vision") or {})
    if not vision.get("corrected"):
        vision["original"] = {
            "dish_name": vision.get("dish_name", ""),
            "portion_g": vision.get("portion_g", 0),
            "size_class": vision.get("size_class", ""),
            "confidence": vision.get("confidence", 0),
        }
    vision["dish_name"] = data.dish_name
    vision["portion_g"] = data.portion_g
    vision["portion_desc"] = f"手动校准：{data.dish_name} {data.portion_g:g}g"[:50]
    vision["corrected"] = True
    # 命中营养库 → 食材明细换成库内标准食材（食材不可手动编辑；未命中保留模型明细）
    entry = foods_data.find_food(data.dish_name)
    if entry:
        vision["ingredients"] = [{"name": name, "portion_g": 0} for name in entry.get("ingredients", [])]
    result["vision"] = vision
    result["nutrition"] = nut

    # 校准后按新菜名重算匹配信息：确认过的不再要求确认
    class _Corrected:
        is_food = True
        dish_name = data.dish_name
        confidence = 1.0

    match_info = recognition.evaluate(_Corrected(), nut)
    match_info["needs_confirm"] = False
    match_info["reason_codes"] = []
    match_info["reasons"] = []
    match_info["corrected"] = True
    result["match"] = match_info

    # 单菜校准：把多菜结果收敛成一条，避免统计重复
    result["dishes"] = [{
        "dish_name": data.dish_name,
        "category": vision.get("category", ""),
        "size_class": "normal",
        "portion_g": data.portion_g,
        "portion_desc": vision["portion_desc"],
        "confidence": 1.0,
        "ingredients": vision.get("ingredients") or [],
        "nutrition": nut,
        "match": match_info,
        "corrected": True,
    }]
    old_ids = list(result.get("food_record_ids") or [])
    if len(old_ids) > 1:
        for extra_id in old_ids[1:]:
            extra = db.get(FoodRecord, extra_id)
            if extra:
                db.delete(extra)
        result["food_record_ids"] = old_ids[:1]

    rec.result = result
    rec.dish_name = data.dish_name
    rec.calories = nut["calories"]
    rec.is_food = True

    # 已保存 → 同步更新饮食记录
    if rec.food_record_id:
        record = db.get(FoodRecord, rec.food_record_id)
        if record:
            record.dish_name = data.dish_name
            record.portion_g = data.portion_g
            record.portion_desc = vision["portion_desc"]
            record.ingredients = vision["ingredients"]
            record.calories = nut["calories"]
            record.protein_g = nut["protein_g"]
            record.fat_g = nut["fat_g"]
            record.carb_g = nut["carb_g"]

    db.commit()
    db.refresh(rec)
    return recognition_dict(rec)


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

    result = dict(rec.result or {})
    vision = dict(result.get("vision") or {})
    dishes = result.get("dishes") or []
    now = datetime.now()
    ids: list[int] = []

    if dishes:
        # 多菜：一道菜一条饮食记录（统计口径按菜品算），积分仍按「每张照片」只给一次
        for dish in dishes:
            nut = dish.get("nutrition") or {}
            record = FoodRecord(
                user_id=user.id,
                recognition_id=rec.id,
                source="photo",
                dish_name=dish.get("dish_name") or "未知食物",
                ingredients=dish.get("ingredients") or [],
                portion_g=dish.get("portion_g") or 0,
                portion_desc=dish.get("portion_desc") or "",
                calories=nut.get("calories", 0),
                protein_g=nut.get("protein_g", 0),
                fat_g=nut.get("fat_g", 0),
                carb_g=nut.get("carb_g", 0),
                meal_type=data.meal_type,
                image_path=rec.image_path,
                eaten_at=now,
                eaten_date=now.date(),
            )
            db.add(record)
            db.flush()
            ids.append(record.id)
    else:
        # 兼容旧识别记录：整张图一条记录
        nut = result.get("nutrition", {})
        record = FoodRecord(
            user_id=user.id,
            recognition_id=rec.id,
            source="photo",
            dish_name=rec.dish_name or "未知食物",
            ingredients=vision.get("ingredients"),
            portion_g=vision.get("portion_g") or 0,
            portion_desc=vision.get("portion_desc") or "",
            calories=nut.get("calories", 0),
            protein_g=nut.get("protein_g", 0),
            fat_g=nut.get("fat_g", 0),
            carb_g=nut.get("carb_g", 0),
            meal_type=data.meal_type,
            image_path=rec.image_path,
            eaten_at=now,
            eaten_date=now.date(),
        )
        db.add(record)
        db.flush()
        ids.append(record.id)

    result["food_record_ids"] = ids
    rec.result = result
    rec.food_record_id = ids[0]
    pts = points.award(db, user, "save_recognition",
                       description=f"保存识别记录：{rec.dish_name}", ref_type="food_record", ref_id=ids[0])
    unlock_all(db, user)   # 累计保存饮食记录成就
    db.commit()
    return {"food_record_id": ids[0], "food_record_ids": ids, "points_awarded": pts or 0}


@router.post("/{recognition_id}/rerun")
def rerun_recognition(
    recognition_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """重新识别：复用原图再跑一次模型，更新识别结果；
    若已保存为饮食记录，同步更新该饮食记录（保证溯源与统计一致）"""
    rec = db.get(Recognition, recognition_id)
    if not rec or rec.user_id != user.id:
        raise HTTPException(status_code=404, detail="识别记录不存在")
    if not rec.image_path:
        raise HTTPException(status_code=400, detail="该记录缺少原图，无法重新识别")
    image_file = settings.UPLOAD_DIR / rec.image_path
    if not image_file.exists():
        raise HTTPException(status_code=400, detail="原图文件已丢失，无法重新识别")

    image_bytes = image_file.read_bytes()
    content_type = "image/jpeg"
    if image_file.suffix == ".png":
        content_type = "image/png"
    elif image_file.suffix == ".webp":
        content_type = "image/webp"

    profile_ctx = _build_profile_ctx(db, user.id)
    vision, dishes, nut, raw_portion_g, provider = _analyze_image(image_bytes, content_type, profile_ctx)

    rec.provider = provider.active_name
    rec.result = {
        "vision": {**vision.model_dump(exclude={"dishes"}), "dishes": dishes, "portion_g_raw": raw_portion_g},
        "nutrition": nut,
        "match": dishes[0]["match"] if dishes else recognition.evaluate(vision, nut),
        "dishes": dishes,
    }
    rec.dish_name = vision.dish_name
    rec.calories = nut["calories"]
    rec.confidence = vision.confidence
    rec.is_food = vision.is_food

    # 已保存 → 同步更新饮食记录
    if rec.food_record_id:
        record = db.get(FoodRecord, rec.food_record_id)
        if record:
            record.dish_name = vision.dish_name
            record.portion_g = vision.portion_g or 0
            record.portion_desc = vision.portion_desc or ""
            record.ingredients = vision.ingredients
            record.calories = nut["calories"]
            record.protein_g = nut["protein_g"]
            record.fat_g = nut["fat_g"]
            record.carb_g = nut["carb_g"]

    db.commit()
    db.refresh(rec)
    return recognition_dict(rec)


@router.delete("/{recognition_id}")
def delete_recognition(
    recognition_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除识别记录。

    约束（保护溯源）：已生成饮食记录的识别禁止删除——
    否则饮食记录会失去原始图片依据；只有"尚未保存为饮食记录的识别草稿"允许删除。
    """
    rec = db.get(Recognition, recognition_id)
    if not rec or rec.user_id != user.id:
        raise HTTPException(status_code=404, detail="识别记录不存在")
    if rec.food_record_id:
        raise HTTPException(status_code=409, detail="该识别已生成饮食记录，不能删除（会断裂溯源）")
    # 删除识别记录时顺带清理上传的图片文件（草稿无引用）
    if rec.image_path:
        img = settings.UPLOAD_DIR / rec.image_path
        if img.exists():
            try:
                img.unlink()
            except OSError:
                pass
    db.delete(rec)
    db.commit()
    return {"ok": True}
