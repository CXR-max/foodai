"""方案七项营养自检 + 饮食记录归属

Revision ID: f7d2b4c9e1a3
Revises: c2f9a8b41d63
Create Date: 2026-11-17

新增字段：
- plan_days.nutrition    这天餐食实排的七项营养（生成时算好存下，不再重算）
- plan_days.day_flags    生成留痕：结构项降级 / 越界 / 数据缺失
- food_records.origin    饮食归属：plan / extra / unknown（区分「方案内」与「额外加餐」）
- food_records.fiber_g / sodium_mg / sugar_g  结构项，判定钠与糖要用
"""
from alembic import op
import sqlalchemy as sa


revision = "f7d2b4c9e1a3"
down_revision = "c2f9a8b41d63"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plan_days", sa.Column("nutrition", sa.JSON(), nullable=True))
    op.add_column("plan_days", sa.Column("day_flags", sa.JSON(), nullable=True))
    op.add_column("food_records", sa.Column("origin", sa.String(length=10), nullable=True))
    op.add_column("food_records", sa.Column("fiber_g", sa.Float(), nullable=True))
    op.add_column("food_records", sa.Column("sodium_mg", sa.Float(), nullable=True))
    op.add_column("food_records", sa.Column("sugar_g", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("food_records", "sugar_g")
    op.drop_column("food_records", "sodium_mg")
    op.drop_column("food_records", "fiber_g")
    op.drop_column("food_records", "origin")
    op.drop_column("plan_days", "day_flags")
    op.drop_column("plan_days", "nutrition")
