"""账号矩阵：account_profiles 存每个账号的昵称、头像和备注

Revision ID: 8f3a1d6c9b27
Revises: e2d7b4a91c36
Create Date: 2026-11-04 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f3a1d6c9b27"
down_revision: str | Sequence[str] | None = "e2d7b4a91c36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_profiles",
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("nick", sa.Text(), server_default="", nullable=False),
        sa.Column("avatar", sa.Text(), server_default="", nullable=False),
        sa.Column("ip_location", sa.Text(), server_default="", nullable=False),
        sa.Column("seller_credit", sa.Text(), server_default="", nullable=False),
        sa.Column("followers", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.Double(), nullable=True),
        sa.Column("remark", sa.Text(), server_default="", nullable=False),
        sa.PrimaryKeyConstraint("account_id", name=op.f("account_profiles_pkey")),
    )


def downgrade() -> None:
    op.drop_table("account_profiles")
