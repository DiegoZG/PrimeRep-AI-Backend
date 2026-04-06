"""add workout_day_history table

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2025-01-25 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create workout_day_history table."""
    op.create_table(
        "workout_day_history",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workout_date", sa.Date(), nullable=False),
        sa.Column("day_type", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Unique constraint: one record per user per date
    op.create_unique_constraint(
        "uq_workout_day_history_user_date",
        "workout_day_history",
        ["user_id", "workout_date"],
    )

    # Index for fast lookups by user
    op.create_index(
        "ix_workout_day_history_user_id",
        "workout_day_history",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop workout_day_history table."""
    op.drop_index("ix_workout_day_history_user_id", table_name="workout_day_history")
    op.drop_constraint(
        "uq_workout_day_history_user_date", "workout_day_history", type_="unique"
    )
    op.drop_table("workout_day_history")

