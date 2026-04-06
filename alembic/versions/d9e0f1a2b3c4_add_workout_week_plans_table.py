"""add workout_week_plans table

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-01-26 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, Sequence[str], None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create workout_week_plans table."""
    op.create_table(
        "workout_week_plans",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("week_start_date", sa.Date(), nullable=False),
        sa.Column("days_per_week", sa.Integer(), nullable=False),
        sa.Column("plan_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Unique constraint: one plan per user per week
    op.create_unique_constraint(
        "uq_workout_week_plans_user_week",
        "workout_week_plans",
        ["user_id", "week_start_date"],
    )

    # Index for fast lookups by user
    op.create_index(
        "ix_workout_week_plans_user_id",
        "workout_week_plans",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop workout_week_plans table."""
    op.drop_index("ix_workout_week_plans_user_id", table_name="workout_week_plans")
    op.drop_constraint(
        "uq_workout_week_plans_user_week", "workout_week_plans", type_="unique"
    )
    op.drop_table("workout_week_plans")

