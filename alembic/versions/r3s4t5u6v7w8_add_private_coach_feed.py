"""add private coach feed and notification outbox

Revision ID: r3s4t5u6v7w8
Revises: q2r3s4t5u6v7
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "r3s4t5u6v7w8"
down_revision = "q2r3s4t5u6v7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "coach_feed_items",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(), nullable=False),
        sa.Column("evidence_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("evidence_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("protected_facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("ai_title", sa.String(), nullable=True),
        sa.Column("ai_body", sa.Text(), nullable=True),
        sa.Column("ai_detail", sa.Text(), nullable=True),
        sa.Column("ai_status", sa.String(), nullable=False, server_default="not_eligible"),
        sa.Column("ai_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ai_claim_token", sa.String(), nullable=True),
        sa.Column("target_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidation_reason", sa.String(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("kind IN ('progression', 'recovery', 'missed_workout', 'personal_record', 'consistency', 'program_review', 'upcoming_workout')", name="ck_coach_feed_items_kind"),
        sa.CheckConstraint("ai_status IN ('not_eligible', 'pending', 'processing', 'enriched', 'fallback')", name="ck_coach_feed_items_ai_status"),
        sa.CheckConstraint("priority BETWEEN 0 AND 100", name="ck_coach_feed_items_priority"),
        sa.CheckConstraint("ai_attempt_count >= 0", name="ck_coach_feed_items_ai_attempts"),
        sa.CheckConstraint("invalidation_reason IS NULL OR invalidation_reason IN ('source_mutated', 'candidate_absent', 'stale_target')", name="ck_coach_feed_items_invalidation_reason"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "dedupe_key", name="uq_coach_feed_items_user_dedupe"),
    )
    op.create_index("ix_coach_feed_items_visible", "coach_feed_items", ["user_id", "dismissed_at", "expires_at", "priority", "created_at", "id"])
    op.create_index("ix_coach_feed_items_ai_queue", "coach_feed_items", ["ai_status", "kind", "expires_at", "user_id"])
    op.create_index("ix_coach_feed_items_expires_at", "coach_feed_items", ["expires_at"])

    op.create_table(
        "coach_preferences",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reminder_time", sa.Time(), nullable=False, server_default="08:00:00"),
        sa.Column("time_zone", sa.String(), nullable=False, server_default="UTC"),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciliation_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciliation_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reconciled_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("reconciliation_revision >= reconciled_revision AND reconciled_revision >= 0", name="ck_coach_preferences_reconciliation_revisions"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_coach_preferences_dirty", "coach_preferences", ["reconciliation_requested_at", "user_id"], postgresql_where=sa.text("reconciliation_requested_at IS NOT NULL"))
    op.create_index("ix_coach_preferences_notifications", "coach_preferences", [sa.text("last_reconciled_at ASC NULLS FIRST"), "user_id"], postgresql_where=sa.text("notifications_enabled IS TRUE"))

    op.create_table(
        "coach_notification_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("feed_item_id", sa.String(), nullable=False),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_delivery_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("job_type IN ('scheduled_daily', 'equipment_review')", name="ck_coach_notification_jobs_type"),
        sa.CheckConstraint("status IN ('pending', 'processing', 'sent', 'retry', 'failed', 'cancelled')", name="ck_coach_notification_jobs_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_coach_notification_jobs_attempts"),
        sa.ForeignKeyConstraint(["feed_item_id"], ["coach_feed_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feed_item_id", name="uq_coach_notification_jobs_item"),
    )
    op.create_index("ix_coach_notification_jobs_due", "coach_notification_jobs", ["status", "scheduled_at", "retry_at"])
    op.create_index("uq_coach_notification_jobs_daily", "coach_notification_jobs", ["user_id", "local_delivery_date"], unique=True, postgresql_where=sa.text("job_type = 'scheduled_daily' AND status != 'cancelled'"))

    op.create_table(
        "coach_notification_deliveries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("push_token_hash", sa.String(), nullable=False),
        sa.Column("expo_ticket_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ticketed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_receipt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("receipt_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('pending', 'ticketed', 'delivered', 'retry', 'failed')", name="ck_coach_notification_deliveries_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_coach_notification_deliveries_attempts"),
        sa.CheckConstraint("receipt_attempts >= 0", name="ck_coach_notification_deliveries_receipt_attempts"),
        sa.ForeignKeyConstraint(["job_id"], ["coach_notification_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "push_token_hash", name="uq_coach_deliveries_job_token"),
    )
    op.create_index("ix_coach_notification_deliveries_expo_ticket_id", "coach_notification_deliveries", ["expo_ticket_id"])
    op.create_index("ix_coach_notification_deliveries_receipt", "coach_notification_deliveries", ["status", "next_receipt_at"])
    op.create_index("ix_coach_notification_deliveries_created_at", "coach_notification_deliveries", ["created_at"])


def downgrade():
    op.drop_index("ix_coach_notification_deliveries_created_at", table_name="coach_notification_deliveries", if_exists=True)
    op.drop_index("ix_coach_notification_deliveries_receipt", table_name="coach_notification_deliveries")
    op.drop_index("ix_coach_notification_deliveries_expo_ticket_id", table_name="coach_notification_deliveries")
    op.drop_table("coach_notification_deliveries")
    op.drop_index("uq_coach_notification_jobs_daily", table_name="coach_notification_jobs")
    op.drop_index("ix_coach_notification_jobs_due", table_name="coach_notification_jobs")
    op.drop_table("coach_notification_jobs")
    op.drop_index("ix_coach_preferences_notifications", table_name="coach_preferences", if_exists=True)
    op.drop_index("ix_coach_preferences_dirty", table_name="coach_preferences", if_exists=True)
    op.drop_table("coach_preferences")
    op.drop_index("ix_coach_feed_items_expires_at", table_name="coach_feed_items", if_exists=True)
    op.drop_index("ix_coach_feed_items_ai_queue", table_name="coach_feed_items", if_exists=True)
    op.drop_index("ix_coach_feed_items_visible", table_name="coach_feed_items")
    op.drop_table("coach_feed_items")
