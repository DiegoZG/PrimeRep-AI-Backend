"""add revoked refresh tokens

Revision ID: k6l7m8n9o0p1
Revises: j5k6l7m8n9o0
"""
from alembic import op
import sqlalchemy as sa

revision = "k6l7m8n9o0p1"
down_revision = "j5k6l7m8n9o0"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "revoked_refresh_tokens",
        sa.Column("jti", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_revoked_refresh_tokens_user_id", "revoked_refresh_tokens", ["user_id"])

def downgrade():
    op.drop_index("ix_revoked_refresh_tokens_user_id", table_name="revoked_refresh_tokens")
    op.drop_table("revoked_refresh_tokens")
