"""第三方货源记下用哪个解析方案取的

Revision ID: b83e6d1f4a27
Revises: 5d0c3e9b71a4
Create Date: 2026-11-19 21:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b83e6d1f4a27"
down_revision: str | Sequence[str] | None = "5d0c3e9b71a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "third_party_sources",
        sa.Column("parser", sa.Text(), server_default="generic", nullable=False),
    )
    # 以前简篇、美篇读的是文章数据，其余都是按普通网页取的
    op.execute(
        "UPDATE third_party_sources SET parser = 'jianpian' WHERE platform IN ('简篇', '美篇')"
    )


def downgrade() -> None:
    op.drop_column("third_party_sources", "parser")
