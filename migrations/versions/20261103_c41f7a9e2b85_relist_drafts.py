"""草稿也用来重新上架：drafts 加 relist_item_id

Revision ID: c41f7a9e2b85
Revises: a3c8e5d71f20
Create Date: 2026-11-03 23:55:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c41f7a9e2b85"
down_revision: str | Sequence[str] | None = "a3c8e5d71f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("drafts", sa.Column("relist_item_id", sa.Text(), nullable=True))


def downgrade() -> None:
    # 降级后它们就是普通的发新商品草稿，没有原商品的 editDetail 可以照着拼
    op.execute("UPDATE drafts SET origin = NULL WHERE relist_item_id IS NOT NULL")
    op.drop_column("drafts", "relist_item_id")
