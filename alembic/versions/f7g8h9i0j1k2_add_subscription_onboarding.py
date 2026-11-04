"""Add independently targeted onboarding subscriptions and durable enrollment."""
from alembic import op
import sqlalchemy as sa

revision = "f7g8h9i0j1k2"
down_revision = "e6f7g8h9i0j1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("feature_flag_configs", sa.Column("subscriptions_onboarding_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("feature_flag_configs", sa.Column("subscriptions_onboarding_audience", sa.String(16), nullable=False, server_default="everyone"))
    op.create_check_constraint("ck_feature_flag_onboarding_audience", "feature_flag_configs", "subscriptions_onboarding_audience IN ('everyone', 'selected')")
    op.add_column("feature_flag_user_overrides", sa.Column("subscriptions_onboarding_enabled", sa.Boolean(), nullable=True))
    op.drop_constraint("ck_feature_flag_override_has_rule", "feature_flag_user_overrides", type_="check")
    op.create_check_constraint("ck_feature_flag_override_has_rule", "feature_flag_user_overrides", "subscriptions_purchases_enabled IS NOT NULL OR subscriptions_enforcement_enabled IS NOT NULL OR subscriptions_onboarding_enabled IS NOT NULL")
    op.add_column("subscription_states", sa.Column("onboarding_enrolled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("subscription_states", sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("subscription_states", "onboarding_completed_at")
    op.drop_column("subscription_states", "onboarding_enrolled_at")
    op.execute("DELETE FROM feature_flag_user_overrides WHERE subscriptions_purchases_enabled IS NULL AND subscriptions_enforcement_enabled IS NULL")
    op.drop_constraint("ck_feature_flag_override_has_rule", "feature_flag_user_overrides", type_="check")
    op.create_check_constraint("ck_feature_flag_override_has_rule", "feature_flag_user_overrides", "subscriptions_purchases_enabled IS NOT NULL OR subscriptions_enforcement_enabled IS NOT NULL")
    op.drop_column("feature_flag_user_overrides", "subscriptions_onboarding_enabled")
    op.drop_constraint("ck_feature_flag_onboarding_audience", "feature_flag_configs", type_="check")
    op.drop_column("feature_flag_configs", "subscriptions_onboarding_audience")
    op.drop_column("feature_flag_configs", "subscriptions_onboarding_enabled")
