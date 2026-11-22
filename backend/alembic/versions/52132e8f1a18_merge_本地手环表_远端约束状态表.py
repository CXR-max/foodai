"""merge: 本地手环表 + 远端约束状态表

Revision ID: 52132e8f1a18
Revises: c7d9e1f2a3b4, e5f9c3d2b8a1
Create Date: 2026-11-19 11:10:37.724575

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '52132e8f1a18'
down_revision: Union[str, Sequence[str], None] = ('c7d9e1f2a3b4', 'e5f9c3d2b8a1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
