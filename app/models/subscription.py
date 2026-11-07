from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String

from app.core.database import Base


class SubscriptionState(Base):
    __tablename__ = "subscription_states"

    environment = Column(String(32), primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    billing_status = Column(String(16), nullable=False, default="unknown")
    verification_status = Column(String(16), nullable=False, default="unverified")
    verified_at = Column(DateTime(timezone=True), nullable=True)
    provider_snapshot_at = Column(DateTime(timezone=True), nullable=True)
    product_id = Column(String(200), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    grace_expires_at = Column(DateTime(timezone=True), nullable=True)
    will_renew = Column(Boolean, nullable=True)
    management_url = Column(String(500), nullable=True)
    next_check_at = Column(DateTime(timezone=True), nullable=True)
    onboarding_enrolled_at = Column(DateTime(timezone=True), nullable=True)
    onboarding_completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_subscription_states_due", "environment", "next_check_at"),)


class SubscriptionTransitionGrant(Base):
    __tablename__ = "subscription_transition_grants"

    environment = Column(String(32), primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    launch_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class BillingWebhookEvent(Base):
    __tablename__ = "billing_webhook_events"

    environment = Column(String(32), primary_key=True)
    event_id = Column(String(200), primary_key=True)
    event_type = Column(String(80), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False)


class BillingJob(Base):
    __tablename__ = "billing_jobs"

    environment = Column(String(32), primary_key=True)
    user_id = Column(String, primary_key=True)
    kind = Column(String(16), primary_key=True)
    status = Column(String(16), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False)
    requested_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(String(80), nullable=True)

    __table_args__ = (Index("ix_billing_jobs_due", "environment", "status", "next_attempt_at"),)
