"""repair resumable idempotency constraints

Revision ID: m8n9o0p1q2r3
Revises: l7m8n9o0p1q2
"""
from alembic import op


revision = "m8n9o0p1q2r3"
down_revision = "l7m8n9o0p1q2"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_workout_sessions_client_session_id'
            ) THEN
                ALTER TABLE workout_sessions
                DROP CONSTRAINT uq_workout_sessions_client_session_id;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_workout_sessions_user_client_session'
            ) THEN
                ALTER TABLE workout_sessions
                ADD CONSTRAINT uq_workout_sessions_user_client_session
                UNIQUE (user_id, client_session_id);
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_set_logs_client_operation_id'
            ) THEN
                ALTER TABLE set_logs
                DROP CONSTRAINT uq_set_logs_client_operation_id;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_set_logs_session_client_operation'
            ) THEN
                ALTER TABLE set_logs
                ADD CONSTRAINT uq_set_logs_session_client_operation
                UNIQUE (session_id, client_operation_id);
            END IF;
        END $$;
        """
    )


def downgrade():
    # The preceding revision already defines the composite constraints. This
    # repair migration only brings databases that received obsolete global
    # constraints back to that declared schema, so there is nothing to undo.
    pass
