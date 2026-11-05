"""拼多多货源：pdd_goods 存浏览器插件从拼多多商品页导入的商品，drafts 记下照哪个货源建的

Revision ID: 1afd2eb6634d
Revises: b6e1f4c8a2d9
Create Date: 2026-11-05 08:06:17.626885
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1afd2eb6634d"
down_revision: str | Sequence[str] | None = "b6e1f4c8a2d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pdd_goods",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("goods_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("mall_name", sa.Text(), server_default="", nullable=False),
        sa.Column("min_price_cent", sa.BigInteger(), nullable=True),
        sa.Column("max_price_cent", sa.BigInteger(), nullable=True),
        sa.Column("data", sa.Text(), server_default="{}", nullable=False),
        sa.Column("imported_at", sa.Double(), nullable=False),
        sa.Column("updated_at", sa.Double(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("pdd_goods_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "goods_id", name=op.f("pdd_goods_pkey")),
    )
    op.create_index("pdd_goods_user", "pdd_goods", ["user_id", "updated_at"], unique=False)
    op.add_column("drafts", sa.Column("pdd_goods_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("drafts", "pdd_goods_id")
    op.drop_index("pdd_goods_user", table_name="pdd_goods")
    op.drop_table("pdd_goods")
