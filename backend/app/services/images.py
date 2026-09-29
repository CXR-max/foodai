"""图片预处理：送给视觉模型前先压缩，减少上传体积和视觉 token（识别更快）。

只做两件小事：长边缩到 ≤1024、转成 JPEG。任何异常都原样返回，
保证"压缩失败也不影响识别"。
"""
import io
import logging

from PIL import Image, ImageOps

logger = logging.getLogger("llm")

MAX_EDGE = 1024      # 长边最大像素（菜品细节足够，且显著减少视觉 token）
JPEG_QUALITY = 80    # JPEG 质量（肉眼几乎无差，体积大幅下降）


def compress_for_vision(image_bytes: bytes, content_type: str) -> tuple[bytes, str]:
    """压缩图片；返回 (新字节, 新content_type)。压不小或出错则返回原图。"""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)          # 手机照片的旋转标记转正
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if max(img.size) > MAX_EDGE:
            img.thumbnail((MAX_EDGE, MAX_EDGE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        out = buf.getvalue()
        if len(out) >= len(image_bytes):
            return image_bytes, content_type        # 压不动就保留原图
        logger.info("图片压缩：%.0fKB → %.0fKB（%dx%d）",
                    len(image_bytes) / 1024, len(out) / 1024, img.width, img.height)
        return out, "image/jpeg"
    except Exception as e:  # noqa: BLE001 —— 压缩失败不能拖垮识别
        logger.warning("图片压缩失败，按原图处理：%s", e)
        return image_bytes, content_type
