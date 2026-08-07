"""add exercise content columns and exercise_questions table

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-02 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add long-form content columns to exercises and create exercise_questions."""

    op.add_column("exercises", sa.Column("how_to", sa.Text(), nullable=True))
    op.add_column("exercises", sa.Column("why_it_works", sa.Text(), nullable=True))
    op.add_column("exercises", sa.Column("common_mistakes", sa.Text(), nullable=True))
    op.add_column("exercises", sa.Column("beginner_notes", sa.Text(), nullable=True))

    op.create_table(
        "exercise_questions",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "exercise_id",
            sa.String(),
            sa.ForeignKey("exercises.id"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_exercise_questions_user_created",
        "exercise_questions",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_exercise_questions_user_exercise",
        "exercise_questions",
        ["user_id", "exercise_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_exercise_questions_user_exercise", table_name="exercise_questions")
    op.drop_index("ix_exercise_questions_user_created", table_name="exercise_questions")
    op.drop_table("exercise_questions")

    op.drop_column("exercises", "beginner_notes")
    op.drop_column("exercises", "common_mistakes")
    op.drop_column("exercises", "why_it_works")
    op.drop_column("exercises", "how_to")
