"""建表：原来 SQLite 里的六张表，类型换成 PostgreSQL 的

布尔列用 boolean，时间戳用 double precision，往前翻消息的游标（毫秒）和金额用 bigint。

Revision ID: d588b1255f0a
Revises:
Create Date: 2026-11-01 01:32:37.809443
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d588b1255f0a"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("cid", sa.Text(), nullable=False),
        sa.Column("peer_id", sa.Text(), server_default="", nullable=False),
        sa.Column("peer_name", sa.Text(), server_default="", nullable=False),
        sa.Column("peer_type", sa.Text(), server_default="", nullable=False),
        sa.Column("named_at", sa.Double(), nullable=True),
        sa.Column("visible", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("muted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("biz_type", sa.Text(), server_default="", nullable=False),
        sa.Column("item_id", sa.Text(), server_default="", nullable=False),
        sa.Column("item_title", sa.Text(), server_default="", nullable=False),
        sa.Column("item_pic", sa.Text(), server_default="", nullable=False),
        sa.Column("order_id", sa.Text(), server_default="", nullable=False),
        sa.Column("unread", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.Double(), nullable=True),
        sa.Column("fetched_at", sa.Double(), nullable=True),
        sa.Column("older_cursor", sa.BigInteger(), nullable=True),
        sa.Column("older_more", sa.Boolean(), nullable=True),
        sa.Column("first_seen_at", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "cid", name=op.f("conversations_pkey")),
    )
    op.create_table(
        "item_ops",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("item_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("item_ops_pkey")),
    )
    op.create_index("item_ops_item", "item_ops", ["account_id", "item_id"], unique=False)
    op.create_table(
        "items",
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("item_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("price_cent", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=True),
        sa.Column("pic_url", sa.Text(), server_default="", nullable=False),
        sa.Column("want_count", sa.Integer(), nullable=True),
        sa.Column("browse_count", sa.Integer(), nullable=True),
        sa.Column("collect_count", sa.Integer(), nullable=True),
        sa.Column("on_platform", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("first_seen_at", sa.Double(), nullable=False),
        sa.Column("synced_at", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "item_id", name=op.f("items_pkey")),
    )
    op.create_table(
        "messages",
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("cid", sa.Text(), nullable=False),
        sa.Column("sender_id", sa.Text(), server_default="", nullable=False),
        sa.Column("sender_name", sa.Text(), server_default="", nullable=False),
        sa.Column("sent_at", sa.Double(), nullable=True),
        sa.Column("content_type", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column("item_id", sa.Text(), server_default="", nullable=False),
        sa.Column("raw", sa.Text(), server_default="{}", nullable=False),
        sa.Column("received_at", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "message_id", name=op.f("messages_pkey")),
    )
    op.create_index("messages_conv", "messages", ["account_id", "cid", "sent_at"], unique=False)
    op.create_table(
        "order_ops",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("order_ops_pkey")),
    )
    op.create_index("order_ops_order", "order_ops", ["account_id", "order_id"], unique=False)
    op.create_table(
        "orders",
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column("item_id", sa.Text(), server_default="", nullable=False),
        sa.Column("title", sa.Text(), server_default="", nullable=False),
        sa.Column("pic_url", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("status_text", sa.Text(), server_default="", nullable=False),
        sa.Column("price_cent", sa.BigInteger(), nullable=True),
        sa.Column("quantity", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("buyer_id", sa.Text(), server_default="", nullable=False),
        sa.Column("buyer_nick", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.Double(), nullable=True),
        sa.Column("raw", sa.Text(), server_default="{}", nullable=False),
        sa.Column("first_seen_at", sa.Double(), nullable=False),
        sa.Column("synced_at", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "order_id", name=op.f("orders_pkey")),
    )


def downgrade() -> None:
    op.drop_table("orders")
    op.drop_index("order_ops_order", table_name="order_ops")
    op.drop_table("order_ops")
    op.drop_index("messages_conv", table_name="messages")
    op.drop_table("messages")
    op.drop_table("items")
    op.drop_index("item_ops_item", table_name="item_ops")
    op.drop_table("item_ops")
    op.drop_table("conversations")
