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
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY client_session_id ORDER BY created_at, id
            ) AS position
            FROM workout_sessions
            WHERE client_session_id IS NOT NULL
        )
        UPDATE workout_sessions
        SET client_session_id = NULL
        WHERE id IN (SELECT id FROM ranked WHERE position > 1);

        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY client_operation_id ORDER BY logged_at, id
            ) AS position
            FROM set_logs
            WHERE client_operation_id IS NOT NULL
        )
        UPDATE set_logs
        SET client_operation_id = NULL
        WHERE id IN (SELECT id FROM ranked WHERE position > 1);

        ALTER TABLE workout_sessions
        DROP CONSTRAINT IF EXISTS uq_workout_sessions_user_client_session;
        ALTER TABLE workout_sessions
        ADD CONSTRAINT uq_workout_sessions_client_session_id UNIQUE (client_session_id);

        ALTER TABLE set_logs
        DROP CONSTRAINT IF EXISTS uq_set_logs_session_client_operation;
        ALTER TABLE set_logs
        ADD CONSTRAINT uq_set_logs_client_operation_id UNIQUE (client_operation_id);
        """
    )
