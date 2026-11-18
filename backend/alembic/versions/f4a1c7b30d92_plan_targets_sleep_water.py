"""plan targets + sleep/water logs

Revision ID: f4a1c7b30d92
Revises: e7c1a9d24b30
Create Date: 2026-11-16 16:30:00.000000

内容：
1) diet_plans 增加计划级目标 targets / 沿用来源 reused_from / 版本 plan_version
2) plan_days 增加运动处方 exercises / 睡眠方案 sleep / 饮水目标 water_ml / 当日目标覆盖 targets
3) health_profiles 增加作息与饮水目标（排睡眠方案用）
4) 新建 sleep_logs（一晚一条）、water_logs（一天多条）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4a1c7b30d92'
down_revision: Union[str, Sequence[str], None] = 'e7c1a9d24b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 计划主表
    with op.batch_alter_table('diet_plans') as batch:
        batch.add_column(sa.Column('targets', sa.JSON(), nullable=True))
        batch.add_column(sa.Column('reused_from', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('plan_version', sa.Integer(), nullable=True, server_default='1'))

    # 2) 计划每日明细
    with op.batch_alter_table('plan_days') as batch:
        batch.add_column(sa.Column('exercises', sa.JSON(), nullable=True))
        batch.add_column(sa.Column('sleep', sa.JSON(), nullable=True))
        batch.add_column(sa.Column('water_ml', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('targets', sa.JSON(), nullable=True))

    # 3) 健康档案：作息与饮水目标
    with op.batch_alter_table('health_profiles') as batch:
        batch.add_column(sa.Column('sleep_time', sa.String(length=5), nullable=True))
        batch.add_column(sa.Column('wake_time', sa.String(length=5), nullable=True))
        batch.add_column(sa.Column('water_target_ml', sa.Integer(), nullable=True))

    # 4) 睡眠记录（一晚一条：手动 / 手环共用）
    op.create_table(
        'sleep_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('sleep_date', sa.Date(), nullable=False),
        sa.Column('bedtime', sa.DateTime(), nullable=True),
        sa.Column('wake_at', sa.DateTime(), nullable=True),
        sa.Column('duration_min', sa.Integer(), nullable=True),
        sa.Column('deep_min', sa.Integer(), nullable=True),
        sa.Column('rem_min', sa.Integer(), nullable=True),
        sa.Column('awake_min', sa.Integer(), nullable=True),
        sa.Column('quality_score', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=True),
        sa.Column('plan_day_id', sa.Integer(), nullable=True),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['plan_day_id'], ['plan_days.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'sleep_date', name='uq_sleep_user_date'),
    )
    op.create_index('ix_sleep_logs_user_id', 'sleep_logs', ['user_id'])
    op.create_index('ix_sleep_logs_sleep_date', 'sleep_logs', ['sleep_date'])
    op.create_index('ix_sleep_logs_user_date', 'sleep_logs', ['user_id', 'sleep_date'])

    # 5) 饮水记录（一天多条）
    op.create_table(
        'water_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('log_date', sa.Date(), nullable=False),
        sa.Column('ml', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_water_logs_user_id', 'water_logs', ['user_id'])
    op.create_index('ix_water_logs_log_date', 'water_logs', ['log_date'])
    op.create_index('ix_water_logs_user_date', 'water_logs', ['user_id', 'log_date'])


def downgrade() -> None:
    op.drop_index('ix_water_logs_user_date', table_name='water_logs')
    op.drop_index('ix_water_logs_log_date', table_name='water_logs')
    op.drop_index('ix_water_logs_user_id', table_name='water_logs')
    op.drop_table('water_logs')

    op.drop_index('ix_sleep_logs_user_date', table_name='sleep_logs')
    op.drop_index('ix_sleep_logs_sleep_date', table_name='sleep_logs')
    op.drop_index('ix_sleep_logs_user_id', table_name='sleep_logs')
    op.drop_table('sleep_logs')

    with op.batch_alter_table('health_profiles') as batch:
        batch.drop_column('water_target_ml')
        batch.drop_column('wake_time')
        batch.drop_column('sleep_time')

    with op.batch_alter_table('plan_days') as batch:
        batch.drop_column('targets')
        batch.drop_column('water_ml')
        batch.drop_column('sleep')
        batch.drop_column('exercises')

    with op.batch_alter_table('diet_plans') as batch:
        batch.drop_column('plan_version')
        batch.drop_column('reused_from')
        batch.drop_column('targets')
