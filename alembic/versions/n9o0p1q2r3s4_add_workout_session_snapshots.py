"""add immutable workout session snapshots

Revision ID: n9o0p1q2r3s4
Revises: m8n9o0p1q2r3
"""
from alembic import op
from sqlalchemy.dialects import postgresql
import sqlalchemy as sa


revision = "n9o0p1q2r3s4"
down_revision = "m8n9o0p1q2r3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workout_sessions", sa.Column("workout_snapshot", postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column("workout_sessions", "workout_snapshot")
