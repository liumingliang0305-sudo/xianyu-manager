"""AI 对话可选带一个提示词模板

Revision ID: e5a19c7d3b42
Revises: b83e6d1f4a27
Create Date: 2026-11-19 23:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a19c7d3b42"
down_revision: str | Sequence[str] | None = "b83e6d1f4a27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_chats", sa.Column("template_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_chats", "template_id")
