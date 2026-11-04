"""Add verified subscription snapshots, transition grants and billing inbox/jobs."""
from alembic import op
import sqlalchemy as sa

revision = "e6f7g8h9i0j1"
down_revision = "d5e6f7g8h9i0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "subscription_states",
        sa.Column("environment", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("billing_status", sa.String(16), nullable=False),
        sa.Column("verification_status", sa.String(16), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("product_id", sa.String(200), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("will_renew", sa.Boolean(), nullable=True),
        sa.Column("management_url", sa.String(500), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "subscription_transition_grants",
        sa.Column("environment", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("launch_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_subscription_states_due", "subscription_states", ["environment", "next_check_at"])
    op.create_table(
        "billing_webhook_events",
        sa.Column("environment", sa.String(32), primary_key=True),
        sa.Column("event_id", sa.String(200), primary_key=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "billing_jobs",
        sa.Column("environment", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(80), nullable=True),
    )
    op.create_index("ix_billing_jobs_due", "billing_jobs", ["environment", "status", "next_attempt_at"])


def downgrade():
    op.drop_table("billing_jobs")
    op.drop_table("billing_webhook_events")
    op.drop_table("subscription_transition_grants")
    op.drop_table("subscription_states")
