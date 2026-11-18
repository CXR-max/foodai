"""人脸比对 Provider

- mock  : 本地演示。默认直接放行（仅用于没有云端 key 时跑通流程），
          设 FACE_MOCK_STRICT=true 会退化成感知哈希比对（同图通过，换人大概率不通过）
- baidu : 百度智能云「人脸比对 v3」，1:1 比对，返回 0~100 相似度

注意：活体检测在端上做（眨眼/动作），服务端只负责"这张脸是不是这个用户"。
金融级场景必须换成百度人脸核身 H5 SDK（返回服务端可校验的 token），
不能只信前端传来的 liveness 布尔值，详见 docs/face-login.md。
"""
import base64
import io
import logging
import time
from abc import ABC, abstractmethod

import httpx
from PIL import Image

from app.core.config import settings

logger = logging.getLogger("face")


class FaceError(Exception):
    """人脸调用失败（配置错、网络错、云端返回错误码）"""


class FaceProvider(ABC):
    name = "base"

    @abstractmethod
    def compare(self, ref_image: bytes, cap_image: bytes) -> tuple[float, bool]:
        """底库照 vs 现场照，返回 (相似度 0~100, 是否通过)"""


def _ahash(image_bytes: bytes, size: int = 8) -> list[int]:
    """感知哈希：缩成 8x8 灰度，比均值大的记 1。用来做本地近似比对。"""
    img = Image.open(io.BytesIO(image_bytes)).convert("L").resize((size, size))
    pixels = list(img.getdata())
    avg = sum(pixels) / len(pixels)
    return [1 if p > avg else 0 for p in pixels]


def _hash_similarity(a: bytes, b: bytes) -> float:
    """1 - 汉明距离/位数，0~1"""
    ha, hb = _ahash(a), _ahash(b)
    same = sum(1 for x, y in zip(ha, hb) if x == y)
    return same / len(ha)


class MockFaceProvider(FaceProvider):
    """演示用：没有云端 key 也能把整条链路跑通"""

    name = "mock"

    def compare(self, ref_image: bytes, cap_image: bytes) -> tuple[float, bool]:
        if settings.FACE_MOCK_STRICT:
            sim = _hash_similarity(ref_image, cap_image)
            score = round(sim * 100, 1)
            logger.info("mock 严格模式：相似度 %.1f", score)
            return score, score >= settings.FACE_MATCH_THRESHOLD
        logger.warning("FACE_PROVIDER=mock：未做真实人脸比对，仅演示流程（生产必须换成 baidu）")
        return 99.0, True


class BaiduFaceProvider(FaceProvider):
    """百度智能云人脸比对 v3（有免费额度）

    开通：https://console.bce.baidu.com/ai/#/ai/face/overview/index
    拿到 API Key / Secret Key 填进 .env 的 BAIDU_FACE_API_KEY / BAIDU_FACE_SECRET_KEY
    """

    name = "baidu"

    def __init__(self) -> None:
        self._token = ""
        self._expire_at = 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._expire_at:
            return self._token
        if not (settings.BAIDU_FACE_API_KEY and settings.BAIDU_FACE_SECRET_KEY):
            raise FaceError("未配置 BAIDU_FACE_API_KEY / BAIDU_FACE_SECRET_KEY")
        try:
            resp = httpx.post(
                "https://aip.baidubce.com/oauth/2.0/token",
                params={
                    "grant_type": "client_credentials",
                    "client_id": settings.BAIDU_FACE_API_KEY,
                    "client_secret": settings.BAIDU_FACE_SECRET_KEY,
                },
                timeout=10,
            )
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise FaceError(f"百度 token 请求失败：{e}") from e
        if "access_token" not in data:
            raise FaceError(f"百度 token 返回异常：{data}")
        self._token = data["access_token"]
        self._expire_at = time.time() + int(data.get("expires_in", 2592000)) - 300
        return self._token

    def compare(self, ref_image: bytes, cap_image: bytes) -> tuple[float, bool]:
        # 底库照是静态照片，不能开活体控制（否则必被判非活体）；
        # 现场照开 LOW 级活体，配合端上眨眼检测，挡掉纯图片攻击
        body = [
            {
                "image": base64.b64encode(ref_image).decode(),
                "image_type": "BASE64",
                "face_type": "LIVE",
                "quality_control": "LOW",
                "liveness_control": "NONE",
            },
            {
                "image": base64.b64encode(cap_image).decode(),
                "image_type": "BASE64",
                "face_type": "LIVE",
                "quality_control": "LOW",
                "liveness_control": "NORMAL",
            },
        ]
        # 免费额度 QPS 很低（常见 1~2），并发或连点时百度会返回 error_code=18，
        # 这里做短退避重试，避免用户点一次就报"服务不可用"
        data: dict = {}
        for attempt in range(3):
            try:
                resp = httpx.post(
                    "https://aip.baidubce.com/rest/2.0/face/v3/match",
                    params={"access_token": self._access_token()},
                    json=body,
                    timeout=15,
                )
                data = resp.json()
            except FaceError:
                raise
            except Exception as e:  # noqa: BLE001
                raise FaceError(f"百度人脸比对请求失败：{e}") from e

            if data.get("error_code") == 18 and attempt < 2:
                time.sleep(0.6 * (attempt + 1))
                continue
            break

        if data.get("error_code") == 18:
            raise FaceError("百度接口限流（QPS 超限），请稍后重试")
        if data.get("error_code") != 0:
            raise FaceError(f"百度人脸比对失败：{data.get('error_code')} {data.get('error_msg')}")
        score = float(data["result"]["score"])
        return score, score >= settings.FACE_MATCH_THRESHOLD


_provider: FaceProvider | None = None


def get_face_provider() -> FaceProvider:
    """按配置返回单例。改 .env 后重启生效。"""
    global _provider
    if _provider is None:
        kind = (settings.FACE_PROVIDER or "mock").strip().lower()
        _provider = BaiduFaceProvider() if kind == "baidu" else MockFaceProvider()
        logger.info("人脸 Provider = %s", _provider.name)
    return _provider
