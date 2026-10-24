"""add session-owned exercise arrangements

Revision ID: a2b3c4d5e6f7
Revises: z1a2b3c4d5e6
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a2b3c4d5e6f7"
down_revision = "z1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workout_sessions", sa.Column("effective_workout", postgresql.JSONB(), nullable=True))
    op.add_column("workout_sessions", sa.Column("arrangement_revision", sa.Integer(), server_default="0", nullable=False))
    op.add_column("workout_session_operations", sa.Column("payload_fingerprint", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("workout_session_operations", "payload_fingerprint")
    op.drop_column("workout_sessions", "arrangement_revision")
    op.drop_column("workout_sessions", "effective_workout")
