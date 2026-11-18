"""问题反馈带截图

Revision ID: 5d0c3e9b71a4
Revises: 76caee8984cf
Create Date: 2026-11-19 01:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d0c3e9b71a4"
down_revision: str | Sequence[str] | None = "76caee8984cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("feedback", sa.Column("images", sa.Text(), server_default="[]", nullable=False))


def downgrade() -> None:
    op.drop_column("feedback", "images")
