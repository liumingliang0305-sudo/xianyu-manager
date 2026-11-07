"""搜索暂存：search_stash 存搜索页上点「暂存」留下的商品

Revision ID: 93c4acef158a
Revises: 1afd2eb6634d
Create Date: 2026-11-06 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "93c4acef158a"
down_revision: str | Sequence[str] | None = "1afd2eb6634d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_stash",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("item_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("price_cent", sa.BigInteger(), nullable=False),
        sa.Column("orig_price_cent", sa.BigInteger(), nullable=True),
        sa.Column("pic_url", sa.Text(), server_default="", nullable=False),
        sa.Column("area", sa.Text(), server_default="", nullable=False),
        sa.Column("seller_nick", sa.Text(), server_default="", nullable=False),
        sa.Column("want_count", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.Double(), nullable=True),
        sa.Column("free_ship", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("tags", sa.Text(), server_default="[]", nullable=False),
        sa.Column("seller_tags", sa.Text(), server_default="[]", nullable=False),
        sa.Column("keyword", sa.Text(), server_default="", nullable=False),
        sa.Column("saved_at", sa.Double(), nullable=False),
        sa.Column("updated_at", sa.Double(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("search_stash_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "item_id", name=op.f("search_stash_pkey")),
    )
    op.create_index("search_stash_user", "search_stash", ["user_id", "saved_at"], unique=False)


def downgrade() -> None:
    op.drop_index("search_stash_user", table_name="search_stash")
    op.drop_table("search_stash")
