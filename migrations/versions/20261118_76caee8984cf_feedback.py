"""问题反馈；用户加管理员标记，已有的第一个用户设成管理员

Revision ID: 76caee8984cf
Revises: 47fa824f061f
Create Date: 2026-11-18 09:23:55.105199
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "76caee8984cf"
down_revision: str | Sequence[str] | None = "47fa824f061f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page", sa.Text(), server_default="", nullable=False),
        sa.Column("user_agent", sa.Text(), server_default="", nullable=False),
        sa.Column("revision", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=True),
        sa.Column("issue_url", sa.Text(), nullable=True),
        sa.Column("pushed_at", sa.Double(), nullable=True),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.Column("updated_at", sa.Double(), nullable=False),
        sa.CheckConstraint("kind IN ('bug', 'idea', 'other')", name="feedback_kind"),
        sa.CheckConstraint("status IN ('open', 'done')", name="feedback_status"),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 5000", name="feedback_content_length"
        ),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 100", name="feedback_title_length"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("feedback_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("feedback_pkey")),
    )
    op.create_index("feedback_user", "feedback", ["user_id", "id"], unique=False)
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    # 和 xym users add 建第一个用户时一样：最早建的用户是管理员
    op.execute("UPDATE users SET is_admin = true WHERE id = (SELECT min(id) FROM users)")


def downgrade() -> None:
    op.drop_column("users", "is_admin")
    op.drop_index("feedback_user", table_name="feedback")
    op.drop_table("feedback")
