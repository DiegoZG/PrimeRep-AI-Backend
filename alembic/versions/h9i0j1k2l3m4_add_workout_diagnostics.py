"""Store private workout diagnostics and anonymous daily observations."""
from alembic import op
import sqlalchemy as sa

revision = "h9i0j1k2l3m4"
down_revision = "g8h9i0j1k2l3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workout_diagnostics",
        sa.Column("environment", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("trace_id", sa.String(36)),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("platform", sa.String(8), nullable=False),
        sa.Column("app_version", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.CheckConstraint("event_type != 'operation_terminal' OR trace_id IS NOT NULL", name="ck_workout_diagnostic_terminal_trace"),
    )
    op.create_index("uq_workout_diagnostic_terminal", "workout_diagnostics", ["environment", "user_id", "trace_id"], unique=True,
                    postgresql_where=sa.text("event_type = 'operation_terminal'"), sqlite_where=sa.text("event_type = 'operation_terminal'"))
    op.create_index("ix_workout_diagnostics_retention", "workout_diagnostics", ["environment", "received_at"])
    dimensions = ["environment", "bucket_date", "platform", "app_version", "event_type", "operation_type", "outcome",
                  "error_category", "acknowledgment_kind", "recovered_after_restart", "finish_stage", "metric", "histogram_bucket"]
    lengths = {"environment": 32, "platform": 8, "app_version": 64, "event_type": 32, "operation_type": 32,
               "outcome": 16, "error_category": 24, "acknowledgment_kind": 16, "recovered_after_restart": 8,
               "finish_stage": 16, "metric": 24, "histogram_bucket": 24}
    op.create_table(
        "workout_diagnostic_aggregates",
        sa.Column("id", sa.Integer(), primary_key=True),
        *(sa.Column(name, sa.Date() if name == "bucket_date" else sa.String(lengths[name]), nullable=False) for name in dimensions),
        sa.Column("sample_count", sa.BigInteger(), nullable=False),
        sa.Column("total", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(*dimensions, name="uq_workout_diagnostic_aggregate"),
        sa.CheckConstraint("sample_count >= 0 AND total >= 0", name="ck_workout_diagnostic_aggregate_counts"),
    )
    op.create_index("ix_workout_diagnostic_aggregates_retention", "workout_diagnostic_aggregates", ["environment", "bucket_date"])


def downgrade():
    op.drop_table("workout_diagnostic_aggregates")
    op.drop_table("workout_diagnostics")
