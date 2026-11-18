"""plan review + plan day edit trace

Revision ID: c2f9a8b41d63
Revises: b8e4d17c9a52
Create Date: 2026-11-16 17:25:00.000000

内容：
1) diet_plans 增加 review（方案停用时的执行评价 JSON）
2) plan_days 增加 edit_count / last_edited_at（用户改过几次，评价里算「调整质量」）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2f9a8b41d63'
down_revision: Union[str, Sequence[str], None] = 'b8e4d17c9a52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('diet_plans') as batch:
        batch.add_column(sa.Column('review', sa.JSON(), nullable=True))

    with op.batch_alter_table('plan_days') as batch:
        batch.add_column(sa.Column('edit_count', sa.Integer(), nullable=True, server_default='0'))
        batch.add_column(sa.Column('last_edited_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('plan_days') as batch:
        batch.drop_column('last_edited_at')
        batch.drop_column('edit_count')
    with op.batch_alter_table('diet_plans') as batch:
        batch.drop_column('review')
