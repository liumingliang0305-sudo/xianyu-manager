"""第三方货源：保存网页正文和图片，AI 对话可选带货源

Revision ID: 47fa824f061f
Revises: a62280aa5c57
Create Date: 2026-11-16 11:14:34.096483
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "47fa824f061f"
down_revision: str | Sequence[str] | None = "a62280aa5c57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "third_party_sources",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), server_default="", nullable=False),
        sa.Column("images", sa.Text(), server_default="[]", nullable=False),
        sa.Column("imported_at", sa.Double(), nullable=False),
        sa.Column("updated_at", sa.Double(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("third_party_sources_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "source_id", name=op.f("third_party_sources_pkey")),
    )
    op.create_index(
        "third_party_sources_user", "third_party_sources", ["user_id", "updated_at"], unique=False
    )
    op.add_column("agent_chats", sa.Column("source_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_chats", "source_id")
    op.drop_index("third_party_sources_user", table_name="third_party_sources")
    op.drop_table("third_party_sources")
