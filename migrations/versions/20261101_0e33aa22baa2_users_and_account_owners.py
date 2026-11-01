"""管理后台的用户：用户、刷新令牌、闲鱼账号归属

以前登录的闲鱼账号没有归属，谁都看不到：xym users add 建第一个用户时归给这个用户，
或者用 xym accounts assign 分。

Revision ID: 0e33aa22baa2
Revises: d588b1255f0a
Create Date: 2026-11-01 05:22:14.732160
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0e33aa22baa2"
down_revision: str | Sequence[str] | None = "d588b1255f0a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("token_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("disabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("plugin_token_hash", sa.Text(), nullable=True),
        sa.Column("plugin_token_at", sa.Double(), nullable=True),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("users_pkey")),
    )
    op.create_index("users_plugin_token", "users", ["plugin_token_hash"], unique=True)
    op.create_index("users_username", "users", ["username"], unique=True)
    op.create_table(
        "account_owners",
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("account_owners_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("account_id", name=op.f("account_owners_pkey")),
    )
    op.create_index("account_owners_user", "account_owners", ["user_id"], unique=False)
    op.create_table(
        "refresh_tokens",
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.Column("used_at", sa.Double(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("refresh_tokens_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("refresh_tokens_pkey")),
    )
    op.create_index("refresh_tokens_user", "refresh_tokens", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("refresh_tokens_user", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index("account_owners_user", table_name="account_owners")
    op.drop_table("account_owners")
    op.drop_index("users_username", table_name="users")
    op.drop_index("users_plugin_token", table_name="users")
    op.drop_table("users")
