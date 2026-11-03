"""发布商品的草稿表 drafts

Revision ID: 5177903fa0ee
Revises: 7b3e9c41d2a6
Create Date: 2026-11-03 05:12:28.279575
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5177903fa0ee"
down_revision: str | Sequence[str] | None = "7b3e9c41d2a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "drafts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("price_cent", sa.BigInteger(), nullable=True),
        sa.Column("orig_price_cent", sa.BigInteger(), nullable=True),
        sa.Column("post_mode", sa.Text(), server_default="free", nullable=False),
        sa.Column("post_price_cent", sa.BigInteger(), nullable=True),
        sa.Column("take_self", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("images", sa.Text(), server_default="[]", nullable=False),
        sa.Column("cards", sa.Text(), server_default="[]", nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("item_id", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.Column("updated_at", sa.Double(), nullable=False),
        sa.Column("published_at", sa.Double(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("drafts_pkey")),
    )
    op.create_index("drafts_account", "drafts", ["account_id", "updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index("drafts_account", table_name="drafts")
    op.drop_table("drafts")
