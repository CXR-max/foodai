"""
数据库连接（SQLite）与请求级会话
- engine：全局唯一的数据库引擎
- SessionLocal：会话工厂，每个请求创建一个会话，用完关闭
- Base：所有 ORM 模型的基类（alembic 迁移依赖它）
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

# check_same_thread=False：FastAPI 多线程访问 SQLite 必需
engine = create_engine(settings.DATABASE_URL, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有模型类的父类"""


def get_db():
    """FastAPI 依赖：给每个请求分配一个数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
