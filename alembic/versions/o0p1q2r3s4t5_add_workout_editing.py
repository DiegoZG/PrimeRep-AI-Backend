"""add workout editing data

Revision ID: o0p1q2r3s4t5
Revises: n9o0p1q2r3s4
"""
from alembic import op
import sqlalchemy as sa


revision = "o0p1q2r3s4t5"
down_revision = "n9o0p1q2r3s4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workout_sessions", sa.Column("workout_note", sa.Text(), nullable=True))
    op.add_column("set_logs", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.add_column("set_logs", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("set_logs", sa.Column("version", sa.Integer(), server_default="1", nullable=False))
    op.execute(
        """
        WITH ranked AS (
          SELECT id, row_number() OVER (
            PARTITION BY session_id, exercise_id, set_number
            ORDER BY logged_at DESC, id DESC
          ) AS position
          FROM set_logs
          WHERE deleted_at IS NULL
        )
        UPDATE set_logs
        SET deleted_at = now()
        FROM ranked
        WHERE set_logs.id = ranked.id AND ranked.position > 1;
        """
    )
    op.create_index("ix_set_logs_active_slot", "set_logs", ["session_id", "exercise_id", "set_number"], unique=True, postgresql_where=sa.text("deleted_at IS NULL"))

    op.create_table(
        "workout_session_operations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), sa.ForeignKey("workout_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_operation_id", sa.String(), nullable=False),
        sa.Column("operation_type", sa.String(), nullable=False),
        sa.Column("target_set_log_id", sa.String(), sa.ForeignKey("set_logs.id", ondelete="SET NULL")),
        sa.Column("target_exercise_id", sa.String(), sa.ForeignKey("exercises.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("session_id", "client_operation_id", name="uq_workout_session_operations_session_client"),
    )
    op.create_table(
        "workout_session_exercise_feedback",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), sa.ForeignKey("workout_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exercise_id", sa.String(), sa.ForeignKey("exercises.id"), nullable=False),
        sa.Column("effort", sa.String()),
        sa.Column("rir", sa.Integer()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("session_id", "exercise_id", name="uq_workout_session_feedback_session_exercise"),
        sa.CheckConstraint("effort IS NULL OR effort IN ('easy', 'right', 'hard')", name="ck_workout_session_feedback_effort"),
        sa.CheckConstraint("rir IS NULL OR rir BETWEEN 0 AND 5", name="ck_workout_session_feedback_rir"),
    )
    op.create_table(
        "user_exercise_notes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exercise_id", sa.String(), sa.ForeignKey("exercises.id"), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "exercise_id", name="uq_user_exercise_notes_user_exercise"),
    )


def downgrade():
    op.drop_table("user_exercise_notes")
    op.drop_table("workout_session_exercise_feedback")
    op.drop_table("workout_session_operations")
    op.drop_index("ix_set_logs_active_slot", table_name="set_logs")
    op.drop_column("set_logs", "deleted_at")
    op.drop_column("set_logs", "updated_at")
    op.execute("ALTER TABLE set_logs DROP COLUMN IF EXISTS version")
    op.drop_column("workout_sessions", "workout_note")
