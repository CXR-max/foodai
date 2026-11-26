"""体检报告：识别导入 / 指标卡 / 个人化分析

对应老师的要求：
- 阈值从个人基线推（分析在 services/checkups.py，这里只负责存取）
- 体检数据要能导进来：拍照、上传 PDF 都能**真识别**（services/checkup_scan.py），
  识别结果只做预填，必须经用户核对后才入库
- 导入后做综合分析，不是一个表格摆着
"""
from __future__ import annotations

import logging
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.checkup import Checkup
from app.models.health_profile import HealthProfile
from app.models.user import User
from app.services import checkup_scan
from app.services import checkups as svc
from app.services.llm.base import LLMError
from app.services.llm.provider import get_llm_provider
from app.services.nutrition import calc_bmr

logger = logging.getLogger("llm")

router = APIRouter(tags=["体检报告"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_SUFFIX = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf"}
CHECKUP_DIR = Path(settings.UPLOAD_DIR) / "checkups"


class CheckupIn(BaseModel):
    check_date: date | None = Field(default=None, description="体检日期，默认今天")
    source: str = Field(default="manual", pattern="^(manual|photo|pdf)$")
    hospital: str | None = Field(default=None, max_length=100)
    file_path: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=200)
    # 允许数字字符串（用户从报告上手抄），后端统一转 float
    metrics: dict[str, float | int | str | None] = Field(default_factory=dict)


def _to_float(v) -> float | None:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _clean_metrics(raw: dict, profile: HealthProfile | None) -> dict:
    """只收目录里的指标，顺手把 BMI / 基础代谢补上（用户不用手算）"""
    out: dict[str, float] = {}
    for k, v in (raw or {}).items():
        if k not in svc.ITEM_MAP:
            continue
        f = _to_float(v)
        if f is not None:
            out[k] = round(f, 3)
    h = out.get("height_cm") or (profile.height_cm if profile else None)
    w = out.get("weight_kg") or (profile.weight_kg if profile else None)
    age = profile.age if profile else None
    gender = profile.gender if profile else None
    if "bmi" not in out and h and w:
        out["bmi"] = round(float(w) / (float(h) / 100.0) ** 2, 1)
    if "bmr_kcal" not in out and h and w:
        bmr = calc_bmr(w, h, age, gender)
        if bmr:
            out["bmr_kcal"] = round(float(bmr))
    return out


@router.get("/checkups/catalog")
def get_catalog(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """指标目录：前端录入表单与指标卡都用这份，保证两边口径一致"""
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    return {"groups": svc.catalog_payload(profile.gender if profile else None)}


@router.get("/checkups/analysis")
def get_analysis(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """日程页小卡和 /m/analysis 详情页共用一份分析"""
    return svc.build_analysis(db, user.id)


@router.get("/checkups")
def list_checkups(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    gender = profile.gender if profile else None
    height = profile.height_cm if profile else None
    rows = (db.query(Checkup)
            .filter(Checkup.user_id == user.id)
            .order_by(Checkup.check_date.desc()).all())
    return {
        "items": [
            {**svc.report_summary(r, gender, height),
             "groups": svc.evaluate(r.metrics, gender, height)["groups"]}
            for r in rows
        ]
    }


@router.post("/checkups")
def save_checkup(data: CheckupIn,
                 user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """新增或覆盖某一天的报告（同一天重复导入按覆盖处理）"""
    day = data.check_date or date.today()
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    metrics = _clean_metrics(data.metrics, profile)
    if not metrics:
        raise HTTPException(status_code=400, detail="至少填一项指标")

    row = (db.query(Checkup)
           .filter(Checkup.user_id == user.id, Checkup.check_date == day).first())
    if row:
        row.metrics = metrics
        row.source = data.source
        row.hospital = data.hospital or row.hospital
        row.file_path = data.file_path or row.file_path
        row.note = data.note or row.note
    else:
        row = Checkup(user_id=user.id, check_date=day, source=data.source,
                      hospital=data.hospital, file_path=data.file_path,
                      note=data.note, metrics=metrics)
        db.add(row)

    # 档案里缺体重/身高/体脂时用体检值补齐（不覆盖用户自己填过的）
    if profile:
        updated = False
        for field in ("weight_kg", "height_cm", "body_fat_pct"):
            if getattr(profile, field, None) in (None, 0) and metrics.get(field):
                setattr(profile, field, metrics[field])
                updated = True
        if updated:
            db.add(profile)

    db.commit()
    db.refresh(row)
    return svc.report_summary(row, profile.gender if profile else None,
                              profile.height_cm if profile else None)


@router.delete("/checkups/{checkup_id}")
def delete_checkup(checkup_id: int,
                   user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    row = (db.query(Checkup)
           .filter(Checkup.id == checkup_id, Checkup.user_id == user.id).first())
    if not row:
        raise HTTPException(status_code=404, detail="报告不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/checkups/scan")
async def scan_checkup(file: UploadFile = File(...),
                       user: User = Depends(get_current_user)):
    """拍照 / 上传 PDF → 真识别成指标，返回**预填结果**给用户核对。

    三条通道（见 services/checkup_scan.py）：
    - 图片：校验 → 压到长边 1600 → 视觉模型
    - 电子版 PDF：抽文字层 → 文字模型（比视觉准，还省额度）
    - 扫描版 PDF：渲染前两页 → 视觉模型 → 多页结果合并

    识别只是一步预填，入库仍然走 POST /checkups（用户核对后的值）。
    模型不可用或没认出来时直接报错，不返回任何编造数据。
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIX:
        raise HTTPException(status_code=400, detail="只支持图片（jpg/png/webp）或 PDF")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件是空的")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件超过 10 MB，请压缩后再传")

    # 原件先留档：识别错了，用户能对着原件核对
    file_path = _save_original(user.id, suffix, content)
    source = "pdf" if suffix == ".pdf" else "photo"
    provider = get_llm_provider()

    try:
        if suffix == ".pdf":
            text, images = checkup_scan.pdf_payload(content)
            if text:
                payloads = [checkup_scan.normalize_result(
                    provider.recognize_checkup(report_text=text), text)]
            else:
                payloads = [
                    checkup_scan.normalize_result(
                        provider.recognize_checkup(image_bytes=checkup_scan.image_payload(img)))
                    for img in images
                ]
        else:
            image = checkup_scan.image_payload(content)
            payloads = [checkup_scan.normalize_result(provider.recognize_checkup(image_bytes=image))]
    except ValueError as exc:          # 文件本身有问题（坏图/加密 PDF/HEIC）
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:            # 模型不可用或输出不合法：如实报错，不给假数据
        logger.warning("体检报告识别失败：%s", exc)
        raise HTTPException(
            status_code=502,
            detail="没能识别出这份报告，可以换一张更清楚的照片，或改用手动填写",
        ) from exc

    result = checkup_scan.merge_payloads(payloads)
    result.update({"file_path": file_path, "filename": file.filename, "source": source})
    return result


def _save_original(user_id: int, suffix: str, content: bytes) -> str:
    """原件落到 uploads/checkups/，返回可访问的相对路径"""
    CHECKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{user_id}_{uuid.uuid4().hex[:12]}{suffix}"
    (CHECKUP_DIR / name).write_bytes(content)
    return f"/uploads/checkups/{name}"
