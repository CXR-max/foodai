"""merge nutrition library and personalized burn heads

Revision ID: 11b79bc57c01
Revises: 5628a46a0a84, c51a9e2d4b07
Create Date: 2026-10-28 16:09:55.620017

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '11b79bc57c01'
down_revision: Union[str, Sequence[str], None] = ('5628a46a0a84', 'c51a9e2d4b07')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
