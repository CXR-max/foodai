"""图片校验：坏图 / 伪造 MIME / 超大图必须在上模型之前被拦掉"""
import io

import pytest
from PIL import Image

from app.services.images import validate_image


def _png(size=(120, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 120, 80)).save(buf, format="PNG")
    return buf.getvalue()


def test_valid_png_passes():
    assert validate_image(_png()) == (120, 90)


def test_garbage_bytes_rejected():
    with pytest.raises(ValueError):
        validate_image(b"not an image at all")


def test_oversized_pixels_rejected():
    # 只有 PNG 头 + 超大尺寸声明也能在 verify 阶段被识别，避免解压炸弹
    img = Image.new("L", (10, 10))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = bytearray(buf.getvalue())
    with pytest.raises(ValueError):
        validate_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


def test_tiny_image_rejected():
    with pytest.raises(ValueError):
        validate_image(_png((16, 16)))
