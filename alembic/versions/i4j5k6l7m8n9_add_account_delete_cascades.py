"""add account delete cascades

Revision ID: i4j5k6l7m8n9
Revises: h3i4j5k6l7m8
Create Date: 2026-08-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "i4j5k6l7m8n9"
down_revision: Union[str, Sequence[str], None] = "h3i4j5k6l7m8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "onboarding_profiles_user_id_fkey",
        "onboarding_profiles",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "onboarding_profiles_user_id_fkey",
        "onboarding_profiles",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "exercises_owner_user_id_fkey",
        "exercises",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "exercises_owner_user_id_fkey",
        "exercises",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "exercises_owner_user_id_fkey",
        "exercises",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "exercises_owner_user_id_fkey",
        "exercises",
        "users",
        ["owner_user_id"],
        ["id"],
    )
    op.drop_constraint(
        "onboarding_profiles_user_id_fkey",
        "onboarding_profiles",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "onboarding_profiles_user_id_fkey",
        "onboarding_profiles",
        "users",
        ["user_id"],
        ["id"],
    )
