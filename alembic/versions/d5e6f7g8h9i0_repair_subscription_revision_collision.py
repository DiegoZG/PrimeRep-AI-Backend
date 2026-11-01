"""Repair schemas installed under the formerly duplicated a2 revision.

Revision ID: d5e6f7g8h9i0
Revises: b3c4d5e6f7g8
"""
from alembic import op
from migration_helpers import ensure_feature_flags, ensure_flag_targeting, ensure_session_arrangements

revision = "d5e6f7g8h9i0"
down_revision = "b3c4d5e6f7g8"
branch_labels = None
depends_on = None


def upgrade():
    ensure_session_arrangements(op)
    ensure_feature_flags(op)
    ensure_flag_targeting(op)


def downgrade():
    # The repair does not own legacy data and cannot determine which side existed before it.
    pass
