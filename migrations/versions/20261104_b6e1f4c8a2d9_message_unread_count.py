"""消息加 unread_count：平台说这条消息还有几个接收方没读，用来显示对方已读、未读

已经存下的消息从原始数据里取（历史消息是 message.unreadCount，推送是 1.8）。

Revision ID: b6e1f4c8a2d9
Revises: 8f3a1d6c9b27
Create Date: 2026-11-04 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6e1f4c8a2d9"
down_revision: str | Sequence[str] | None = "8f3a1d6c9b27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("unread_count", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE messages m SET unread_count = s.v::integer
        FROM (
            SELECT account_id, message_id,
                   COALESCE(raw::json #>> '{message,unreadCount}', raw::json #>> '{1,8}') AS v
            FROM messages
        ) s
        WHERE m.account_id = s.account_id AND m.message_id = s.message_id
          AND s.v ~ '^[0-9]+$'
        """
    )


def downgrade() -> None:
    op.drop_column("messages", "unread_count")
