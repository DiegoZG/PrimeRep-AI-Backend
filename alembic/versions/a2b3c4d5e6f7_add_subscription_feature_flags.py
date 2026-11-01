"""Add environment-scoped subscription feature flags and audit history."""

from alembic import op
import sqlalchemy as sa

revision = "a2b3c4d5e6f7"
down_revision = "z1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "feature_flag_configs",
        sa.Column("environment", sa.String(32), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("subscriptions_purchases_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("subscriptions_enforcement_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("subscriptions_launch_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("launch_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_feature_flag_version_positive"),
        sa.CheckConstraint("NOT subscriptions_enforcement_enabled OR (subscriptions_purchases_enabled AND subscriptions_launch_at IS NOT NULL AND launch_locked)", name="ck_feature_flag_enforcement_ready"),
        sa.CheckConstraint("NOT launch_locked OR subscriptions_launch_at IS NOT NULL", name="ck_feature_flag_locked_launch"),
    )
    op.create_table(
        "feature_flag_audits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("environment", sa.String(32), sa.ForeignKey("feature_flag_configs.environment"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changed_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.UniqueConstraint("environment", "version", name="uq_feature_flag_audit_version"),
    )


def downgrade():
    op.drop_table("feature_flag_audits")
    op.drop_table("feature_flag_configs")
