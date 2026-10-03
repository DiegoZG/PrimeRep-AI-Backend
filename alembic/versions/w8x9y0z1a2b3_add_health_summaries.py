"""Add opt-in health source connections and retained daily summaries."""

from alembic import op
import sqlalchemy as sa


revision = "w8x9y0z1a2b3"
down_revision = "v7w8x9y0z1a2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "health_sources",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source", sa.String(), primary_key=True),
        sa.Column("connection_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("read_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("export_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("connected_at", sa.DateTime(timezone=True)),
        sa.Column("disconnected_at", sa.DateTime(timezone=True)),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("source IN ('apple_health', 'health_connect')", name="ck_health_sources_source"),
        sa.CheckConstraint("connection_revision > 0", name="ck_health_sources_revision"),
    )
    op.create_table(
        "health_preferences",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("primary_source", sa.String()),
        sa.Column("coach_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.CheckConstraint("primary_source IS NULL OR primary_source IN ('apple_health', 'health_connect')", name="ck_health_preferences_primary_source"),
    )
    op.create_table(
        "health_days",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source", sa.String(), primary_key=True),
        sa.Column("local_date", sa.Date(), primary_key=True),
        sa.Column("time_zone", sa.String(), primary_key=True),
        sa.Column("steps", sa.Integer()),
        sa.Column("asleep_minutes", sa.Integer()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id", "source"], ["health_sources.user_id", "health_sources.source"], ondelete="CASCADE"),
        sa.CheckConstraint("source IN ('apple_health', 'health_connect')", name="ck_health_days_source"),
        sa.CheckConstraint("steps IS NULL OR steps >= 0", name="ck_health_days_steps"),
        sa.CheckConstraint("asleep_minutes IS NULL OR asleep_minutes BETWEEN 0 AND 1440", name="ck_health_days_asleep_minutes"),
    )
    op.create_index("ix_health_days_user_date", "health_days", ["user_id", "local_date"])


def downgrade():
    op.drop_index("ix_health_days_user_date", table_name="health_days")
    op.drop_table("health_days")
    op.drop_table("health_preferences")
    op.drop_table("health_sources")
