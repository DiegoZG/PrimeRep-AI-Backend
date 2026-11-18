from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


class AIRequestReservation(Base):
    __tablename__ = "ai_request_reservations"
    id = Column(String(36), primary_key=True)
    environment = Column(String(32), nullable=False, index=True)
    purpose = Column(String(32), nullable=False)
    model = Column(String(80), nullable=False)
    status = Column(String(16), nullable=False)
    reserved_micro_usd = Column(BigInteger, nullable=False)
    accounted_micro_usd = Column(BigInteger, nullable=True)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    reasoning_tokens = Column(Integer)
    created_at = Column(DateTime(timezone=True), nullable=False)
    settled_at = Column(DateTime(timezone=True))


class AISpendPeriod(Base):
    __tablename__ = "ai_spend_periods"
    environment = Column(String(32), primary_key=True)
    period = Column(String(16), primary_key=True)
    committed_micro_usd = Column(BigInteger, nullable=False, default=0)


class EquipmentScanAttempt(Base):
    __tablename__ = "equipment_scan_attempts"
    id = Column(String(36), primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    environment = Column(String(32), nullable=False)
    attempt_key = Column(String(36), nullable=False)
    fingerprint = Column(String(64), nullable=True)
    versions = Column(JSONB, nullable=False)
    settings_version = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False)
    reservation_id = Column(String(36), ForeignKey("ai_request_reservations.id", ondelete="SET NULL"))
    result_json = Column(JSONB)
    error_code = Column(String(64))
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    deadline_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("environment", "user_id", "attempt_key", name="uq_equipment_scan_attempt_key"),
        Index("uq_equipment_scan_active_owner", "environment", "user_id", unique=True, postgresql_where=text("status = 'processing'")),
        Index("ix_equipment_scan_fingerprint", "environment", "user_id", "fingerprint"),
    )


class EquipmentScanQuota(Base):
    __tablename__ = "equipment_scan_quotas"
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    environment = Column(String(32), primary_key=True)
    period = Column(String(32), primary_key=True)
    attempts = Column(Integer, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
