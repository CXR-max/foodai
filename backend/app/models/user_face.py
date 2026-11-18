"""人脸登录底库：一个用户一条记录，存底库照片路径（目录不对外暴露）"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.db.session import Base


class UserFace(Base):
    __tablename__ = "user_faces"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, unique=True, index=True)
    image_path = Column(String(300), nullable=False)
    provider = Column(String(20), nullable=True, default="mock")
    created_at = Column(DateTime, nullable=True, default=datetime.now)
    updated_at = Column(DateTime, nullable=True, default=datetime.now)
