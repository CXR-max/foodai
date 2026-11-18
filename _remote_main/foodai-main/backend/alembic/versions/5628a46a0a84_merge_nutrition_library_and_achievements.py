"""merge nutrition library and achievements heads

Revision ID: 5628a46a0a84
Revises: 03ee2ffd34a1, a1c3e5f7b9d1
Create Date: 2026-10-28 15:00:00.000000

两条独立分支的合并点：
- 03ee2ffd34a1：营养库 7 张表（foods/food_aliases/...）
- a1c3e5f7b9d1：成就体系与新字段
两边都基于 baf27cd8977f，这里只做合并，不产生结构变更。
"""
from typing import Sequence, Union

revision: str = "5628a46a0a84"
down_revision: Union[str, Sequence[str], None] = ("03ee2ffd34a1", "a1c3e5f7b9d1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
