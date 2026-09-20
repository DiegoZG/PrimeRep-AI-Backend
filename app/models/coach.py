import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class CoachFeedItem(Base):
    __tablename__ = "coach_feed_items"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String, nullable=False)
    priority = Column(Integer, nullable=False, default=0, server_default="0")
    dedupe_key = Column(String, nullable=False)
    evidence_fingerprint = Column(String, nullable=False)
    evidence_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    evidence_data = Column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    protected_facts = Column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    title = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    detail = Column(Text, nullable=True)
    ai_title = Column(String, nullable=True)
    ai_body = Column(Text, nullable=True)
    ai_detail = Column(Text, nullable=True)
    ai_status = Column(String, nullable=False, default="not_eligible", server_default="not_eligible")
    ai_attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    ai_locked_at = Column(DateTime(timezone=True), nullable=True)
    ai_claim_token = Column(String, nullable=True)
    target_data = Column(JSONB, nullable=False, default=lambda: {"type": "none"})
    available_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    invalidated_at = Column(DateTime(timezone=True), nullable=True)
    invalidation_reason = Column(String, nullable=True)
    read_at = Column(DateTime(timezone=True), nullable=True)
    dismissed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        CheckConstraint(
            "kind IN ('progression', 'recovery', 'missed_workout', "
            "'personal_record', 'consistency', 'program_review', 'upcoming_workout')",
            name="ck_coach_feed_items_kind",
        ),
        CheckConstraint(
            "ai_status IN ('not_eligible', 'pending', 'processing', 'enriched', 'fallback')",
            name="ck_coach_feed_items_ai_status",
        ),
        CheckConstraint("priority BETWEEN 0 AND 100", name="ck_coach_feed_items_priority"),
        CheckConstraint("ai_attempt_count >= 0", name="ck_coach_feed_items_ai_attempts"),
        CheckConstraint(
            "invalidation_reason IS NULL OR invalidation_reason IN "
            "('source_mutated', 'candidate_absent', 'stale_target')",
            name="ck_coach_feed_items_invalidation_reason",
        ),
        UniqueConstraint("user_id", "dedupe_key", name="uq_coach_feed_items_user_dedupe"),
        Index(
            "ix_coach_feed_items_visible",
            "user_id",
            "dismissed_at",
            "expires_at",
            "priority",
            "created_at",
            "id",
        ),
        Index(
            "ix_coach_feed_items_ai_queue",
            "ai_status",
            "kind",
            "expires_at",
            "user_id",
        ),
        Index("ix_coach_feed_items_expires_at", "expires_at"),
    )


class CoachPreference(Base):
    __tablename__ = "coach_preferences"

    user_id = Column(
        String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, nullable=False
    )
    notifications_enabled = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    reminder_time = Column(Time, nullable=False, server_default="08:00:00")
    time_zone = Column(String, nullable=False, server_default="UTC")
    last_reconciled_at = Column(DateTime(timezone=True), nullable=True)
    reconciliation_requested_at = Column(DateTime(timezone=True), nullable=True)
    reconciliation_revision = Column(Integer, nullable=False, default=0, server_default="0")
    reconciled_revision = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "reconciliation_revision >= reconciled_revision AND reconciled_revision >= 0",
            name="ck_coach_preferences_reconciliation_revisions",
        ),
        Index(
            "ix_coach_preferences_dirty",
            "reconciliation_requested_at",
            "user_id",
            postgresql_where=text("reconciliation_requested_at IS NOT NULL"),
        ),
        Index(
            "ix_coach_preferences_notifications",
            last_reconciled_at.asc().nullsfirst(),
            "user_id",
            postgresql_where=text("notifications_enabled IS TRUE"),
        ),
    )


class CoachNotificationJob(Base):
    __tablename__ = "coach_notification_jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    feed_item_id = Column(
        String, ForeignKey("coach_feed_items.id", ondelete="CASCADE"), nullable=False
    )
    job_type = Column(String, nullable=False)
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    local_delivery_date = Column(Date, nullable=False)
    status = Column(String, nullable=False, default="pending", server_default="pending")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    retry_at = Column(DateTime(timezone=True), nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    item = relationship("CoachFeedItem", foreign_keys=[feed_item_id])

    __table_args__ = (
        CheckConstraint(
            "job_type IN ('scheduled_daily', 'equipment_review')",
            name="ck_coach_notification_jobs_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'sent', 'retry', 'failed', 'cancelled')",
            name="ck_coach_notification_jobs_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_coach_notification_jobs_attempts"),
        UniqueConstraint("feed_item_id", name="uq_coach_notification_jobs_item"),
        Index("ix_coach_notification_jobs_due", "status", "scheduled_at", "retry_at"),
        Index(
            "uq_coach_notification_jobs_daily",
            "user_id",
            "local_delivery_date",
            unique=True,
            postgresql_where=text("job_type = 'scheduled_daily' AND status != 'cancelled'"),
        ),
    )


class CoachNotificationDelivery(Base):
    __tablename__ = "coach_notification_deliveries"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(
        String, ForeignKey("coach_notification_jobs.id", ondelete="CASCADE"), nullable=False
    )
    push_token_hash = Column(String, nullable=False)
    expo_ticket_id = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False, default="pending", server_default="pending")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    retry_at = Column(DateTime(timezone=True), nullable=True)
    ticketed_at = Column(DateTime(timezone=True), nullable=True)
    next_receipt_at = Column(DateTime(timezone=True), nullable=True)
    receipt_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    receipt_checked_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    job = relationship("CoachNotificationJob", foreign_keys=[job_id])

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'ticketed', 'delivered', 'retry', 'failed')",
            name="ck_coach_notification_deliveries_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_coach_notification_deliveries_attempts"),
        CheckConstraint(
            "receipt_attempts >= 0",
            name="ck_coach_notification_deliveries_receipt_attempts",
        ),
        UniqueConstraint("job_id", "push_token_hash", name="uq_coach_deliveries_job_token"),
        Index("ix_coach_notification_deliveries_receipt", "status", "next_receipt_at"),
        Index("ix_coach_notification_deliveries_created_at", "created_at"),
    )
