"""food_records: 份量留痕 + 作废标记

Revision ID: b6f1c8d3e470
Revises: a3c8d5e7b210
Create Date: 2026-11-17

为什么加这五列：
- plan_portion_g / portion_delta_g / portion_source：拍照打卡时菜品可能对、
  份量不对。不落这三列，结算时算不出「比方案多吃的那部分」，
  份量吃多就会被归因成「方案排多了」，处置方向整个反过来。
- voided_at / voided_reason：同一餐拍两次照要互相覆盖，否则摄入翻倍。
  物理删除会断掉识别记录的溯源链，所以打作废标记。

不建 voided_at 索引：低选择性，且所有查询都带 (user_id, eaten_date)，
已有复合索引，再加一个是负担而不是收益。
"""
from alembic import op
import sqlalchemy as sa

revision = "b6f1c8d3e470"
down_revision = "a3c8d5e7b210"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("food_records") as batch:
        batch.add_column(sa.Column("plan_portion_g", sa.Float(), nullable=True))
        batch.add_column(sa.Column("portion_delta_g", sa.Float(), nullable=True))
        batch.add_column(sa.Column("portion_source", sa.String(length=12), nullable=True))
        batch.add_column(sa.Column("voided_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("voided_reason", sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("food_records") as batch:
        batch.drop_column("voided_reason")
        batch.drop_column("voided_at")
        batch.drop_column("portion_source")
        batch.drop_column("portion_delta_g")
        batch.drop_column("plan_portion_g")
