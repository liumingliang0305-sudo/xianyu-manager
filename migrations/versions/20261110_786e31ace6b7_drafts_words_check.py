"""草稿记下上次查平台违禁词的结果和当时描述的哈希，描述没变不再查

Revision ID: 786e31ace6b7
Revises: c1f35680066f
Create Date: 2026-11-10 08:27:16.292300
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "786e31ace6b7"
down_revision: str | Sequence[str] | None = "c1f35680066f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("drafts", sa.Column("words_check", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("drafts", "words_check")
