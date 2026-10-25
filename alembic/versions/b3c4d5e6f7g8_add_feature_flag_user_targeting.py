"""Add per-flag audiences and environment-scoped user overrides."""

from alembic import op
import sqlalchemy as sa

revision = "b3c4d5e6f7g8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("feature_flag_configs", sa.Column(
        "subscriptions_purchases_audience", sa.String(16),
        sa.CheckConstraint("subscriptions_purchases_audience IN ('everyone', 'selected')", name="ck_feature_flag_purchases_audience"),
        nullable=False, server_default="everyone",
    ))
    op.add_column("feature_flag_configs", sa.Column(
        "subscriptions_enforcement_audience", sa.String(16),
        sa.CheckConstraint("subscriptions_enforcement_audience IN ('everyone', 'selected')", name="ck_feature_flag_enforcement_audience"),
        nullable=False, server_default="everyone",
    ))
    with op.batch_alter_table("feature_flag_audits") as batch:
        batch.add_column(sa.Column("scope", sa.String(16), nullable=False, server_default="global"))
        batch.add_column(sa.Column("target_user_id", sa.String(), nullable=True))
        batch.create_foreign_key("fk_feature_flag_audit_target", "users", ["target_user_id"], ["id"], ondelete="SET NULL")
        batch.create_check_constraint("ck_feature_flag_audit_scope", "scope IN ('global', 'user')")
    op.create_table(
        "feature_flag_user_overrides",
        sa.Column("environment", sa.String(32), sa.ForeignKey("feature_flag_configs.environment", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("subscriptions_purchases_enabled", sa.Boolean(), nullable=True),
        sa.Column("subscriptions_enforcement_enabled", sa.Boolean(), nullable=True),
        sa.CheckConstraint("subscriptions_purchases_enabled IS NOT NULL OR subscriptions_enforcement_enabled IS NOT NULL", name="ck_feature_flag_override_has_rule"),
    )


def downgrade():
    op.drop_table("feature_flag_user_overrides")
    with op.batch_alter_table("feature_flag_audits") as batch:
        batch.drop_constraint("fk_feature_flag_audit_target", type_="foreignkey")
        batch.drop_constraint("ck_feature_flag_audit_scope", type_="check")
        batch.drop_column("target_user_id")
        batch.drop_column("scope")
    op.drop_column("feature_flag_configs", "subscriptions_enforcement_audience")
    op.drop_column("feature_flag_configs", "subscriptions_purchases_audience")
