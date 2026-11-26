"""体检报告表（个人化分析的基线数据）

Revision ID: b3f7c2d91e05
Revises: 52132e8f1a18
Create Date: 2026-11-25 21:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f7c2d91e05'
down_revision: Union[str, Sequence[str], None] = '52132e8f1a18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'health_checkups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('check_date', sa.Date(), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=True),
        sa.Column('file_path', sa.String(length=300), nullable=True),
        sa.Column('hospital', sa.String(length=100), nullable=True),
        sa.Column('metrics', sa.JSON(), nullable=True),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'check_date', name='uq_checkup_user_date'),
    )
    op.create_index('ix_health_checkups_user_id', 'health_checkups', ['user_id'], unique=False)
    op.create_index('ix_health_checkups_check_date', 'health_checkups', ['check_date'], unique=False)
    op.create_index('ix_health_checkups_user_date', 'health_checkups', ['user_id', 'check_date'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_health_checkups_user_date', table_name='health_checkups')
    op.drop_index('ix_health_checkups_check_date', table_name='health_checkups')
    op.drop_index('ix_health_checkups_user_id', table_name='health_checkups')
    op.drop_table('health_checkups')
