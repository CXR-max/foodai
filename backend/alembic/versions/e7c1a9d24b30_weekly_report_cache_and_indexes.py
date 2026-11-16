"""weekly report cache + composite indexes

Revision ID: e7c1a9d24b30
Revises: 11b79bc57c01
Create Date: 2026-11-16 14:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7c1a9d24b30'
down_revision: Union[str, Sequence[str], None] = '11b79bc57c01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) AI 周报缓存表（同一用户同一周只调一次模型）
    op.create_table(
        'weekly_report_cache',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('week_start', sa.Date(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('score', sa.Integer(), nullable=True),
        sa.Column('highlights', sa.JSON(), nullable=True),
        sa.Column('suggestions', sa.JSON(), nullable=True),
        sa.Column('source', sa.String(length=40), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'week_start', name='uq_weekly_report_user_week'),
    )
    op.create_index('ix_weekly_report_cache_user_id', 'weekly_report_cache', ['user_id'])
    op.create_index('ix_weekly_report_cache_week_start', 'weekly_report_cache', ['week_start'])

    # 2) 复合索引：统计/日历查询都是「用户 + 日期」组合
    op.create_index('ix_food_records_user_eaten_date', 'food_records', ['user_id', 'eaten_date'])
    op.create_index('ix_check_ins_user_date', 'check_ins', ['user_id', 'check_date'])


def downgrade() -> None:
    op.drop_index('ix_check_ins_user_date', table_name='check_ins')
    op.drop_index('ix_food_records_user_eaten_date', table_name='food_records')
    op.drop_index('ix_weekly_report_cache_week_start', table_name='weekly_report_cache')
    op.drop_index('ix_weekly_report_cache_user_id', table_name='weekly_report_cache')
    op.drop_table('weekly_report_cache')
