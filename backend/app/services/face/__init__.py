"""人脸能力：比对 + 活体（活体在端上做，服务端只做 1:1 比对）"""
from app.services.face.provider import FaceError, FaceProvider, get_face_provider

__all__ = ["FaceError", "FaceProvider", "get_face_provider"]
