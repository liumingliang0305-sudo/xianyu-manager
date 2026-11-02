"""订单分卖出的和买到的：加 side，买家的两列改成"对方"（peer_id、peer_nick）

以前只记卖出的订单，已有的都是 sold。

Revision ID: 7b3e9c41d2a6
Revises: 0e33aa22baa2
Create Date: 2026-11-02 06:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b3e9c41d2a6"
down_revision: str | Sequence[str] | None = "0e33aa22baa2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("side", sa.Text(), server_default="sold", nullable=False))
    op.alter_column("orders", "buyer_id", new_column_name="peer_id")
    op.alter_column("orders", "buyer_nick", new_column_name="peer_nick")


def downgrade() -> None:
    # 回到只有卖出订单的表：买到的删掉
    op.execute("DELETE FROM orders WHERE side = 'bought'")
    op.alter_column("orders", "peer_nick", new_column_name="buyer_nick")
    op.alter_column("orders", "peer_id", new_column_name="buyer_id")
    op.drop_column("orders", "side")
