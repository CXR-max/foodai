"""Alembic 迁移环境（已接入本项目的配置与模型）
改了 models/ 里的表结构后执行：
  python -m alembic revision --autogenerate -m "描述"
  python -m alembic upgrade head
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ---- 接入本项目 ----
from app.core.config import settings          # 读 .env 的数据库地址
from app.db.session import Base
import app.models                              # noqa: F401  导入所有模型，autogenerate 才能发现表

config = context.config
# 用应用配置里的 SQLite 绝对路径覆盖 alembic.ini 的占位符
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
