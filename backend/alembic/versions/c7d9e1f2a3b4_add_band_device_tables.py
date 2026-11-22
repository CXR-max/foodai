"""融合迁移：把 feat/personalized-burn 的手环设备三张表并入主分支迁移链。

主分支头是 b6f1c8d3e470；本迁移只负责 devices / device_metrics / device_samples。
兼容从旧分支融合过来的库：表已存在时直接跳过（用 checkfirst 语义），
这样既支持全新 upgrade，也不会在已有库的 upgrade 里因重复建表报错。
"""
from alembic import op
from sqlalchemy import inspect

revision = "c7d9e1f2a3b4"
down_revision = "b6f1c8d3e470"
branch_labels = None
depends_on = None

TABLES = ("devices", "device_metrics", "device_samples")


def upgrade():
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    if all(t in existing for t in TABLES):
        return
    # 表定义以 app/models/device.py 为唯一来源，避免这里再抄一遍字段导致两边漂移
    from app.db.session import Base
    import app.models.device  # noqa: F401
    from app.models.device import Device, DeviceMetric, DeviceSample
    Base.metadata.create_all(
        bind=bind,
        tables=[Device.__table__, DeviceMetric.__table__, DeviceSample.__table__],
        checkfirst=True,
    )


def downgrade():
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    for t in ("device_samples", "device_metrics", "devices"):
        if t in existing:
            op.drop_table(t)
