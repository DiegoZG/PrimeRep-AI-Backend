"""Persist password reset email before delivery."""

from alembic import op
import sqlalchemy as sa


revision = "u6v7w8x9y0z1"
down_revision = "t5u6v7w8x9y0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("idempotency_key", sa.String(), unique=True, nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("reset_token_id", sa.String(), sa.ForeignKey("password_reset_tokens.id", ondelete="CASCADE")),
        sa.Column("encrypted_message", sa.LargeBinary()),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_email_outbox_due", "email_outbox", ["status", "next_attempt_at"])


def downgrade():
    op.drop_index("ix_email_outbox_due", table_name="email_outbox")
    op.drop_table("email_outbox")
