"""Add per-flag audiences and environment-scoped user overrides."""

from alembic import op
import sqlalchemy as sa

revision = "b3c4d5e6f7g8"
down_revision = "c4d5e6f7g8h9"
branch_labels = None
depends_on = None


def upgrade():
    from migration_helpers import ensure_flag_targeting
    ensure_flag_targeting(op)


def downgrade():
    op.drop_table("feature_flag_user_overrides")
    with op.batch_alter_table("feature_flag_audits") as batch:
        batch.drop_constraint("fk_feature_flag_audit_target", type_="foreignkey")
        batch.drop_constraint("ck_feature_flag_audit_scope", type_="check")
        batch.drop_column("target_user_id")
        batch.drop_column("scope")
    op.drop_column("feature_flag_configs", "subscriptions_enforcement_audience")
    op.drop_column("feature_flag_configs", "subscriptions_purchases_audience")
