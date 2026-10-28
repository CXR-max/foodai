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
MAX_PIXELS = 40_000_000   # 4000 万像素上限（防解压炸弹）
MIN_EDGE = 32        # 太小的图没有识别价值


def validate_image(image_bytes: bytes) -> tuple[int, int]:
    """真解码校验图片：坏图/伪造 MIME/超大图直接抛 ValueError。

    只看客户端 content_type 不够——服务器必须自己解一次，
    否则坏图会白白消耗一次视觉调用（60s + 配额）。
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as probe:
            probe.verify()                      # 只校验完整性，不加载像素
    except Exception as exc:  # noqa: BLE001
        raise ValueError("图片无法解析，请换一张 jpg/png/webp") from exc

    with Image.open(io.BytesIO(image_bytes)) as img:
        width, height = img.size
        fmt = (img.format or "").upper()
    if fmt not in {"JPEG", "PNG", "WEBP"}:
        raise ValueError("图片格式不支持，仅限 jpg/png/webp")
    if width * height > MAX_PIXELS:
        raise ValueError("图片分辨率过大，请压缩后再传")
    if min(width, height) < MIN_EDGE:
        raise ValueError("图片太小，无法识别")
    return width, height


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
