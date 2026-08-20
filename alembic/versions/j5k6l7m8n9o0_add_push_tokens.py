"""add push tokens

Revision ID: j5k6l7m8n9o0
Revises: i4j5k6l7m8n9
Create Date: 2026-08-16 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "j5k6l7m8n9o0"
down_revision: Union[str, Sequence[str], None] = "i4j5k6l7m8n9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "push_tokens",
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "platform IN ('ios', 'android')", name="ck_push_tokens_platform"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index("ix_push_tokens_user_id", "push_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_push_tokens_user_id", table_name="push_tokens")
    op.drop_table("push_tokens")
