"""三张闭环表：饮食偏离 / 处置动作 / 当日模式

Revision ID: a3c8d5e7b210
Revises: f7d2b4c9e1a3
Create Date: 2026-11-17 18:50:00.000000

为什么要这三张表（不是往 food_records 里堆字段）：
- diet_deviations 记事实，一条偏离只产生一次，处置怎么变都不改它
- plan_adjustments 记判断，同一偏离可以多条（先拒绝再改主意），要能回看效果
- day_modes 记例外（聚餐/训练/生理期/生病/出差），只改判定口径，不改事实
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3c8d5e7b210'
down_revision: Union[str, Sequence[str], None] = 'f7d2b4c9e1a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'diet_deviations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('dev_date', sa.Date(), nullable=False),
        sa.Column('kind', sa.String(length=8), nullable=False),
        sa.Column('nutrient', sa.String(length=12), nullable=False),
        sa.Column('actual', sa.Float(), nullable=True),
        sa.Column('limit_value', sa.Float(), nullable=True),
        sa.Column('delta', sa.Float(), nullable=True),
        sa.Column('attribution', sa.String(length=10), nullable=True),
        sa.Column('source', sa.String(length=10), nullable=True),
        sa.Column('severity', sa.String(length=8), nullable=True),
        sa.Column('settlement', sa.String(length=10), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=True),
        sa.Column('day_mode', sa.String(length=12), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('handled_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_diet_deviations_user_id', 'diet_deviations', ['user_id'])
    op.create_index('ix_diet_deviations_user_date', 'diet_deviations', ['user_id', 'dev_date'])

    op.create_table(
        'plan_adjustments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('deviation_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('scope', sa.String(length=12), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=True),
        sa.Column('effective_date', sa.Date(), nullable=True),
        sa.Column('outcome', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('applied_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['deviation_id'], ['diet_deviations.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_plan_adjustments_user_id', 'plan_adjustments', ['user_id'])
    op.create_index('ix_plan_adjustments_user_effective', 'plan_adjustments',
                    ['user_id', 'effective_date'])

    op.create_table(
        'day_modes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('mode_date', sa.Date(), nullable=False),
        sa.Column('mode', sa.String(length=12), nullable=False, server_default='normal'),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('effects', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_day_modes_user_id', 'day_modes', ['user_id'])
    op.create_index('ix_day_modes_user_date', 'day_modes', ['user_id', 'mode_date'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_day_modes_user_date', table_name='day_modes')
    op.drop_index('ix_day_modes_user_id', table_name='day_modes')
    op.drop_table('day_modes')
    op.drop_index('ix_plan_adjustments_user_effective', table_name='plan_adjustments')
    op.drop_index('ix_plan_adjustments_user_id', table_name='plan_adjustments')
    op.drop_table('plan_adjustments')
    op.drop_index('ix_diet_deviations_user_date', table_name='diet_deviations')
    op.drop_index('ix_diet_deviations_user_id', table_name='diet_deviations')
    op.drop_table('diet_deviations')
