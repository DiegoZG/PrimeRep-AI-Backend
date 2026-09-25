"""Preserve password-change notices if reset token records are pruned."""

from alembic import op


revision = "v7w8x9y0z1a2"
down_revision = "u6v7w8x9y0z1"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("email_outbox_reset_token_id_fkey", "email_outbox", type_="foreignkey")
    op.create_foreign_key(
        "email_outbox_reset_token_id_fkey",
        "email_outbox",
        "password_reset_tokens",
        ["reset_token_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint("email_outbox_reset_token_id_fkey", "email_outbox", type_="foreignkey")
    op.create_foreign_key(
        "email_outbox_reset_token_id_fkey",
        "email_outbox",
        "password_reset_tokens",
        ["reset_token_id"],
        ["id"],
        ondelete="CASCADE",
    )
