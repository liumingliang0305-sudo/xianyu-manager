"""自动同步：sync_jobs 存每个账号每种同步的设置和上次结果

Revision ID: e2d7b4a91c36
Revises: c41f7a9e2b85
Create Date: 2026-11-04 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2d7b4a91c36"
down_revision: str | Sequence[str] | None = "c41f7a9e2b85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_jobs",
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("job", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("interval", sa.Double(), nullable=True),
        sa.Column("last_run_at", sa.Double(), nullable=True),
        sa.Column("last_ok_at", sa.Double(), nullable=True),
        sa.Column("last_trigger", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_result", sa.Text(), server_default="{}", nullable=False),
        sa.Column("next_run_at", sa.Double(), nullable=True),
        sa.PrimaryKeyConstraint("account_id", "job", name=op.f("sync_jobs_pkey")),
    )


def downgrade() -> None:
    op.drop_table("sync_jobs")
