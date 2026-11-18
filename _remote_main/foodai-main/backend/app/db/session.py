"""
数据库连接（SQLite）与请求级会话
- engine：全局唯一的数据库引擎
- SessionLocal：会话工厂，每个请求创建一个会话，用完关闭
- Base：所有 ORM 模型的基类（alembic 迁移依赖它）
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

# check_same_thread=False：FastAPI 多线程访问 SQLite 必需
engine = create_engine(settings.DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    """每条连接建立时设 PRAGMA。

    - journal_mode=WAL：读写不互斥，显著缓解并发打卡时 "database is locked"
    - busy_timeout=30s：遇到锁先等，而不是立刻抛错（默认仅 5s）
    - synchronous=NORMAL：WAL 下的推荐值，比 FULL 快很多且断电安全性足够
    只对 SQLite 生效；换成 PostgreSQL/MySQL 时这段会被忽略（连接串里没有 sqlite）。
    """
    if "sqlite" not in settings.DATABASE_URL:
        return
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
    finally:
        cur.close()


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
