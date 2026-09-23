"""Add private profile avatars and per-field profile ownership."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "t5u6v7w8x9y0"
down_revision = "s4t5u6v7w8x9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("onboarding_profiles", sa.Column(
        "profile_managed_fields", postgresql.JSONB(), nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    ))
    op.create_table(
        "user_avatars",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("user_avatars")
    op.drop_column("onboarding_profiles", "profile_managed_fields")
