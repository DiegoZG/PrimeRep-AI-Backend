"""Require fresh workout-export consent for legacy Health connections."""

from alembic import op


revision = "z1a2b3c4d5e6"
down_revision = "y0z1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        DELETE FROM health_days AS day
        USING health_sources AS source
        WHERE day.user_id = source.user_id
          AND day.source = source.source
          AND source.export_enabled = TRUE
          AND source.export_enabled_at IS NULL
          AND source.read_enabled = FALSE
    """)
    op.execute("""
        UPDATE health_preferences AS preference
        SET primary_source = NULL, coach_enabled = FALSE
        FROM health_sources AS source
        WHERE preference.user_id = source.user_id
          AND preference.primary_source = source.source
          AND source.export_enabled = TRUE
          AND source.export_enabled_at IS NULL
          AND source.read_enabled = FALSE
    """)
    op.execute("""
        UPDATE health_sources
        SET export_enabled = FALSE,
            enabled = enabled AND read_enabled,
            connection_revision = connection_revision + 1,
            disconnected_at = CASE WHEN read_enabled THEN disconnected_at ELSE CURRENT_TIMESTAMP END,
            last_successful_sync_at = CASE WHEN read_enabled THEN last_successful_sync_at ELSE NULL END
        WHERE export_enabled = TRUE AND export_enabled_at IS NULL
    """)


def downgrade():
    # Revoked, unverified export consent must not be restored by a downgrade.
    pass
