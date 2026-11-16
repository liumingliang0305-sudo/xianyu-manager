"""提示词模板：按后台用户保存名称和正文

Revision ID: a62280aa5c57
Revises: c1f35680066f
Create Date: 2026-11-16 07:33:23.105636
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a62280aa5c57"
down_revision: str | Sequence[str] | None = "c1f35680066f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompt_templates",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.Column("updated_at", sa.Double(), nullable=False),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 20000", name="prompt_templates_content_length"
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100", name="prompt_templates_name_length"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("prompt_templates_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("prompt_templates_pkey")),
    )
    op.create_index(
        "prompt_templates_user", "prompt_templates", ["user_id", "updated_at", "id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("prompt_templates_user", table_name="prompt_templates")
    op.drop_table("prompt_templates")
