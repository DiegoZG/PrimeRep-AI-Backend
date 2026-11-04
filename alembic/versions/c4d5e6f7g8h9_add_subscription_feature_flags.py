"""Add environment-scoped subscription feature flags and audit history."""

from alembic import op
import sqlalchemy as sa

revision = "c4d5e6f7g8h9"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade():
    from migration_helpers import ensure_feature_flags
    ensure_feature_flags(op)


def downgrade():
    op.drop_table("feature_flag_audits")
    op.drop_table("feature_flag_configs")
