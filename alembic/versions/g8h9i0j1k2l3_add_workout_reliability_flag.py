"""Add independent workout reliability rollout controls."""
from alembic import op
import sqlalchemy as sa

revision = "g8h9i0j1k2l3"
down_revision = "f7g8h9i0j1k2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("feature_flag_configs", sa.Column("workout_reliability_monitoring_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("feature_flag_configs", sa.Column("workout_reliability_monitoring_audience", sa.String(16), nullable=False, server_default="everyone"))
    op.create_check_constraint("ck_feature_flag_monitoring_audience", "feature_flag_configs", "workout_reliability_monitoring_audience IN ('everyone', 'selected')")
    op.add_column("feature_flag_user_overrides", sa.Column("workout_reliability_monitoring_enabled", sa.Boolean(), nullable=True))
    op.drop_constraint("ck_feature_flag_override_has_rule", "feature_flag_user_overrides", type_="check")
    op.create_check_constraint("ck_feature_flag_override_has_rule", "feature_flag_user_overrides", "subscriptions_purchases_enabled IS NOT NULL OR subscriptions_enforcement_enabled IS NOT NULL OR subscriptions_onboarding_enabled IS NOT NULL OR workout_reliability_monitoring_enabled IS NOT NULL")


def downgrade():
    op.execute("DELETE FROM feature_flag_user_overrides WHERE subscriptions_purchases_enabled IS NULL AND subscriptions_enforcement_enabled IS NULL AND subscriptions_onboarding_enabled IS NULL")
    op.drop_constraint("ck_feature_flag_override_has_rule", "feature_flag_user_overrides", type_="check")
    op.create_check_constraint("ck_feature_flag_override_has_rule", "feature_flag_user_overrides", "subscriptions_purchases_enabled IS NOT NULL OR subscriptions_enforcement_enabled IS NOT NULL OR subscriptions_onboarding_enabled IS NOT NULL")
    op.drop_column("feature_flag_user_overrides", "workout_reliability_monitoring_enabled")
    op.drop_constraint("ck_feature_flag_monitoring_audience", "feature_flag_configs", type_="check")
    op.drop_column("feature_flag_configs", "workout_reliability_monitoring_audience")
    op.drop_column("feature_flag_configs", "workout_reliability_monitoring_enabled")
