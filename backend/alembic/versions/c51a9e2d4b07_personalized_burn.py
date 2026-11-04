"""personalized burn: check_ins intensity/met_used/burn_detail + health_profiles body_fat_pct

Revision ID: c51a9e2d4b07
Revises: baf27cd8977f
Create Date: 2026-10-27 15:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c51a9e2d4b07'
down_revision: Union[str, Sequence[str], None] = 'd4e8b2c1a7f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """升级：运动打卡记录消耗计算的强度与明细，档案记录体脂率"""
    op.add_column('check_ins', sa.Column('intensity', sa.String(length=10), nullable=True))
    op.add_column('check_ins', sa.Column('met_used', sa.Float(), nullable=True))
    op.add_column('check_ins', sa.Column('burn_detail', sa.JSON(), nullable=True))
    op.add_column('health_profiles', sa.Column('body_fat_pct', sa.Float(), nullable=True))


def downgrade() -> None:
    """降级：移除上述列"""
    op.drop_column('health_profiles', 'body_fat_pct')
    op.drop_column('check_ins', 'burn_detail')
    op.drop_column('check_ins', 'met_used')
    op.drop_column('check_ins', 'intensity')
