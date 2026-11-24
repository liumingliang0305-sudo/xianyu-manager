"""商品记下浏览、收藏数是什么时候从插件拿来的

Revision ID: 9c2e5b7a4f16
Revises: e5a19c7d3b42
Create Date: 2026-11-24 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c2e5b7a4f16"
down_revision: str | Sequence[str] | None = "e5a19c7d3b42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 已经存过的数不知道是什么时候拿的，留 NULL，页面上当作旧了，下次自动刷新时补上
    op.add_column("items", sa.Column("stats_at", sa.Double(), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "stats_at")
