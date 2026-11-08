"""AI 助手的对话：带的拼多多货源、暂存，PydanticAI 的消息历史

Revision ID: c1f35680066f
Revises: 0834153474ee
Create Date: 2026-11-06 23:34:19.524149
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1f35680066f"
down_revision: str | Sequence[str] | None = "0834153474ee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_chats",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("pdd_goods_id", sa.Text(), nullable=True),
        sa.Column("stash_item_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), server_default="", nullable=False),
        sa.Column("messages", sa.Text(), server_default="[]", nullable=False),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.Column("updated_at", sa.Double(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("agent_chats_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("agent_chats_pkey")),
    )
    op.create_index("agent_chats_user", "agent_chats", ["user_id", "updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index("agent_chats_user", table_name="agent_chats")
    op.drop_table("agent_chats")
