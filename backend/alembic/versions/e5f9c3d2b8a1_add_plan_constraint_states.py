"""add plan_constraint_states

Revision ID: e5f9c3d2b8a1
Revises: d4e8b2c1a7f3
Create Date: 2026-10-28 21:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f9c3d2b8a1'
down_revision: Union[str, Sequence[str], None] = 'd4e8b2c1a7f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'plan_constraint_states',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('source_message_id', sa.Integer(), nullable=True),
        sa.Column('scope', sa.String(length=20), nullable=True),
        sa.Column('valid_from', sa.DateTime(), nullable=True),
        sa.Column('valid_until', sa.DateTime(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('superseded_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_plan_constraint_user_active', 'plan_constraint_states',
                    ['user_id', 'superseded_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_plan_constraint_user_active', table_name='plan_constraint_states')
    op.drop_table('plan_constraint_states')
