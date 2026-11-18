"""成就徽章体系 + 历史数据修复

Revision ID: a1c3e5f7b9d1
Revises: baf27cd8977f
Create Date: 2026-10-27

变更：
1. 新表 achievements / user_achievements（成就徽章解锁制）
2. users 新增 equipped_achievement_id（当前装备的头衔/徽章）
3. redemptions 新增 used_at（券使用时间）；status 为 NULL 的历史兑换 → 'unused'
   （修复"刚兑换就显示已使用"）
4. rewards 新增 kind（券用途类型），并回填已有数据
5. food_records.eaten_date / eaten_at 为 NULL 的历史记录 → 按 created_at 回填
   （修复"保存了饮食记录但统计/日历没数据"）
6. 清理 check_ins 重复任务（重生成方案后重复导入导致的同天多份 pending）
   —— 保留已打卡(done)，未打卡(pending)只留最新一份
7. 商城里用积分购买的 honor（虚拟荣誉）类商品下架 —— 荣誉改为成就解锁制
"""
from alembic import op
import sqlalchemy as sa


revision = "a1c3e5f7b9d1"
down_revision = "baf27cd8977f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "achievements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True, index=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("icon", sa.String(10), nullable=True),
        sa.Column("description", sa.String(200), nullable=True),
        sa.Column("category", sa.String(10), nullable=False, server_default="badge"),
        sa.Column("condition_type", sa.String(30), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "user_achievements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"),
                  nullable=False, index=True),
        sa.Column("achievement_id", sa.Integer(), sa.ForeignKey("achievements.id"),
                  nullable=False),
        sa.Column("unlocked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "achievement_id", name="uq_user_achievement"),
    )
    # SQLite 不支持 ALTER 添加外键约束，这里只加普通整型列（模型里的 FK 仅作元数据，SQLite 不强制）
    op.add_column("users", sa.Column("equipped_achievement_id", sa.Integer(), nullable=True))

    # ---- redemptions：补签卡使用时间 + 修复 status ----
    op.add_column("redemptions", sa.Column("used_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE redemptions SET status = 'unused' WHERE status IS NULL")

    # ---- rewards：新增 kind 并回填 ----
    op.add_column("rewards", sa.Column("kind", sa.String(20), nullable=True))
    op.execute("UPDATE rewards SET kind = 'makeup' WHERE name = '补签卡'")
    op.execute("UPDATE rewards SET kind = 'custom_plan' WHERE name = '方案定制券'")
    op.execute("UPDATE rewards SET kind = 'avatar_frame' WHERE name = '专属头像框'")
    op.execute("UPDATE rewards SET kind = 'physical' WHERE category = 'goods'")
    op.execute("UPDATE rewards SET kind = 'physical' WHERE kind IS NULL")

    # ---- 历史数据修复 ----
    # 用户积分兜底为 0，避免旧数据 NULL 导致加减分报错
    op.execute("UPDATE users SET points_total = 0 WHERE points_total IS NULL")
    # 饮食记录补 eaten 时间，保证统计/日历按天匹配
    op.execute("UPDATE food_records SET eaten_at = COALESCE(eaten_at, created_at), "
               "eaten_date = COALESCE(eaten_date, substr(created_at, 1, 10)) "
               "WHERE eaten_date IS NULL")
    # 虚拟荣誉改为成就解锁制，商城里积分购买的 honor 商品下架
    op.execute("UPDATE rewards SET is_active = 0 WHERE category = 'honor'")

    # ---- 清理重复的打卡任务（重生成方案后重复导入导致）----
    # 规则：同 (user, date, task_key) 保留 done；全 pending 则保留最新一条
    bind = op.get_bind()
    dup_rows = bind.execute(sa.text(
        "SELECT user_id, check_date, task_key FROM check_ins "
        "WHERE task_key IS NOT NULL "
        "GROUP BY user_id, check_date, task_key HAVING COUNT(*) > 1"
    )).fetchall()
    for user_id, check_date, task_key in dup_rows:
        keep = bind.execute(sa.text(
            "SELECT id FROM check_ins "
            "WHERE user_id=:u AND check_date=:d AND task_key=:k "
            "ORDER BY (status='done') DESC, created_at DESC, id DESC LIMIT 1"
        ), {"u": user_id, "d": check_date, "k": task_key}).scalar()
        if keep is None:
            continue
        bind.execute(sa.text(
            "DELETE FROM check_ins "
            "WHERE user_id=:u AND check_date=:d AND task_key=:k AND id != :keep"
        ), {"u": user_id, "d": check_date, "k": task_key, "keep": keep})


def downgrade() -> None:
    op.drop_column("rewards", "kind")
    op.drop_column("users", "equipped_achievement_id")
    op.drop_column("redemptions", "used_at")
    op.drop_table("user_achievements")
    op.drop_table("achievements")
