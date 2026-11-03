"""草稿也用来编辑已有商品：drafts 加 edit_item_id、origin

Revision ID: a3c8e5d71f20
Revises: 5177903fa0ee
Create Date: 2026-11-03 23:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c8e5d71f20"
down_revision: str | Sequence[str] | None = "5177903fa0ee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("drafts", sa.Column("edit_item_id", sa.Text(), nullable=True))
    op.add_column("drafts", sa.Column("origin", sa.Text(), nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM drafts WHERE edit_item_id IS NOT NULL")
    op.drop_column("drafts", "origin")
    op.drop_column("drafts", "edit_item_id")
