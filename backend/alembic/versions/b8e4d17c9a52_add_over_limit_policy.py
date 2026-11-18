"""add over_limit_policy to health_profiles

Revision ID: b8e4d17c9a52
Revises: f4a1c7b30d92
Create Date: 2026-11-16 17:15:00.000000

内容：健康档案增加「热量超限默认处理」偏好
- ask（默认，手动选择）/ portion（按比例缩份量）/ exercise（加运动抵消）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8e4d17c9a52'
down_revision: Union[str, Sequence[str], None] = 'f4a1c7b30d92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('health_profiles') as batch:
        batch.add_column(sa.Column('over_limit_policy', sa.String(length=16),
                                   nullable=True, server_default='ask'))


def downgrade() -> None:
    with op.batch_alter_table('health_profiles') as batch:
        batch.drop_column('over_limit_policy')
