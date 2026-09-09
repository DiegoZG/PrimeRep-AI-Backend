"""add resumable workout sessions

Revision ID: l7m8n9o0p1q2
Revises: k6l7m8n9o0p1
"""
from alembic import op
import sqlalchemy as sa

revision = "l7m8n9o0p1q2"
down_revision = "k6l7m8n9o0p1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workout_sessions", sa.Column("client_session_id", sa.String(), nullable=True))
    op.add_column("workout_sessions", sa.Column("status", sa.String(), nullable=False, server_default="in_progress"))
    op.add_column("workout_sessions", sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.execute("UPDATE workout_sessions SET status = CASE WHEN completed_at IS NULL THEN 'in_progress' ELSE 'completed' END")
    # Historical data may have more than one unfinished row per user. Retain
    # the newest as resumable and mark older rows abandoned before adding the
    # database-level active-session invariant.
    op.execute("""
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY user_id ORDER BY started_at DESC, created_at DESC, id DESC
            ) AS position
            FROM workout_sessions
            WHERE status = 'in_progress'
        )
        UPDATE workout_sessions
        SET status = 'abandoned'
        WHERE id IN (SELECT id FROM ranked WHERE position > 1)
    """)
    # Existing historical sessions predate client IDs. They remain nullable;
    # newly-created resumable sessions always provide one.
    op.create_unique_constraint("uq_workout_sessions_user_client_session", "workout_sessions", ["user_id", "client_session_id"])
    op.create_index("ix_workout_sessions_user_status", "workout_sessions", ["user_id", "status"])
    op.create_index(
        "uq_workout_sessions_one_active_per_user",
        "workout_sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'in_progress'"),
    )
    op.add_column("set_logs", sa.Column("client_operation_id", sa.String(), nullable=True))
    op.create_unique_constraint("uq_set_logs_session_client_operation", "set_logs", ["session_id", "client_operation_id"])


def downgrade():
    op.drop_constraint("uq_set_logs_session_client_operation", "set_logs", type_="unique")
    op.drop_column("set_logs", "client_operation_id")
    op.drop_index("uq_workout_sessions_one_active_per_user", table_name="workout_sessions")
    op.drop_index("ix_workout_sessions_user_status", table_name="workout_sessions")
    op.drop_constraint("uq_workout_sessions_user_client_session", "workout_sessions", type_="unique")
    op.drop_column("workout_sessions", "started_at")
    op.drop_column("workout_sessions", "status")
    op.drop_column("workout_sessions", "client_session_id")
