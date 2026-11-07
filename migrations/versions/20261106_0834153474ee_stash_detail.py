"""搜索暂存记下商品详情：完整描述、全部图片、浏览收藏、状态，拿到详情的时间（浏览器插件从商品页拿）

Revision ID: 0834153474ee
Revises: 93c4acef158a
Create Date: 2026-11-06 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0834153474ee"
down_revision: str | Sequence[str] | None = "93c4acef158a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "search_stash", sa.Column("description", sa.Text(), server_default="", nullable=False)
    )
    op.add_column(
        "search_stash", sa.Column("images", sa.Text(), server_default="[]", nullable=False)
    )
    op.add_column("search_stash", sa.Column("browse_count", sa.Integer(), nullable=True))
    op.add_column("search_stash", sa.Column("collect_count", sa.Integer(), nullable=True))
    op.add_column("search_stash", sa.Column("status", sa.Integer(), nullable=True))
    op.add_column(
        "search_stash", sa.Column("status_text", sa.Text(), server_default="", nullable=False)
    )
    op.add_column("search_stash", sa.Column("detail_at", sa.Double(), nullable=True))


def downgrade() -> None:
    for column in (
        "detail_at",
        "status_text",
        "status",
        "collect_count",
        "browse_count",
        "images",
        "description",
    ):
        op.drop_column("search_stash", column)
