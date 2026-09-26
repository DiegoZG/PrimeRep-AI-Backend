import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, LargeBinary, String, func

from app.core.database import Base


class EmailOutbox(Base):
    __tablename__ = "email_outbox"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    idempotency_key = Column(String, unique=True, nullable=False)
    kind = Column(String, nullable=False)
    reset_token_id = Column(String, ForeignKey("password_reset_tokens.id", ondelete="SET NULL"), nullable=True)
    encrypted_message = Column(LargeBinary, nullable=True)
    status = Column(String, nullable=False, server_default="pending")
    attempts = Column(Integer, nullable=False, server_default="0")
    next_attempt_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_email_outbox_due", status, next_attempt_at),)
