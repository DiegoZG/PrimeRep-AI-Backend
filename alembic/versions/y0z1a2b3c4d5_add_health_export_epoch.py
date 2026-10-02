"""Track workout-export consent independently from health reading."""

from alembic import op
import sqlalchemy as sa


revision = "y0z1a2b3c4d5"
down_revision = "x9y0z1a2b3c4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("health_sources", sa.Column("export_enabled_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("health_sources", "export_enabled_at")
