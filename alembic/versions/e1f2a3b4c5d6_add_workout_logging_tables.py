"""add workout_logging_tables

Revision ID: e1f2a3b4c5d6
Revises: d9e0f1a2b3c4
Create Date: 2026-04-07 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create workout_sessions and set_logs tables."""

    op.create_table(
        "workout_sessions",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Soft reference to the workout plan's workoutDayId (not a FK — plan is denormalized JSON)
        sa.Column("workout_day_id", sa.String(), nullable=False),
        sa.Column("workout_date", sa.Date(), nullable=False),
        sa.Column("day_type", sa.String(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_workout_sessions_user_id",
        "workout_sessions",
        ["user_id"],
    )
    op.create_index(
        "ix_workout_sessions_user_date",
        "workout_sessions",
        ["user_id", "workout_date"],
    )

    op.create_table(
        "set_logs",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column(
            "session_id",
            sa.String(),
            sa.ForeignKey("workout_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "exercise_id",
            sa.String(),
            sa.ForeignKey("exercises.id"),
            nullable=False,
        ),
        sa.Column("set_number", sa.Integer(), nullable=False),
        sa.Column("reps", sa.Integer(), nullable=False),
        # Null for bodyweight exercises
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column(
            "logged_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_set_logs_session_id",
        "set_logs",
        ["session_id"],
    )
    op.create_index(
        "ix_set_logs_exercise_id",
        "set_logs",
        ["exercise_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_set_logs_exercise_id", table_name="set_logs")
    op.drop_index("ix_set_logs_session_id", table_name="set_logs")
    op.drop_table("set_logs")

    op.drop_index("ix_workout_sessions_user_date", table_name="workout_sessions")
    op.drop_index("ix_workout_sessions_user_id", table_name="workout_sessions")
    op.drop_table("workout_sessions")
