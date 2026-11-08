from sqlalchemy import BigInteger, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, text

from app.core.database import Base


class WorkoutDiagnostic(Base):
    __tablename__ = "workout_diagnostics"

    environment = Column(String(32), primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    event_id = Column(String(36), primary_key=True)
    trace_id = Column(String(36), nullable=True)
    event_type = Column(String(32), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False)
    platform = Column(String(8), nullable=False)
    app_version = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)

    __table_args__ = (
        Index("uq_workout_diagnostic_terminal", "environment", "user_id", "trace_id", unique=True,
              postgresql_where=text("event_type = 'operation_terminal'"), sqlite_where=text("event_type = 'operation_terminal'")),
        Index("ix_workout_diagnostics_retention", "environment", "received_at"),
        CheckConstraint("event_type != 'operation_terminal' OR trace_id IS NOT NULL", name="ck_workout_diagnostic_terminal_trace"),
    )


class WorkoutDiagnosticAggregate(Base):
    __tablename__ = "workout_diagnostic_aggregates"

    id = Column(Integer, primary_key=True)
    environment = Column(String(32), nullable=False)
    bucket_date = Column(Date, nullable=False)
    platform = Column(String(8), nullable=False)
    app_version = Column(String(64), nullable=False)
    event_type = Column(String(32), nullable=False)
    operation_type = Column(String(32), nullable=False)
    outcome = Column(String(16), nullable=False)
    error_category = Column(String(24), nullable=False)
    acknowledgment_kind = Column(String(16), nullable=False)
    recovered_after_restart = Column(String(8), nullable=False)
    finish_stage = Column(String(16), nullable=False)
    metric = Column(String(24), nullable=False)
    histogram_bucket = Column(String(24), nullable=False)
    sample_count = Column(BigInteger, nullable=False)
    total = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("environment", "bucket_date", "platform", "app_version", "event_type", "operation_type", "outcome",
                         "error_category", "acknowledgment_kind", "recovered_after_restart", "finish_stage", "metric", "histogram_bucket",
                         name="uq_workout_diagnostic_aggregate"),
        Index("ix_workout_diagnostic_aggregates_retention", "environment", "bucket_date"),
        CheckConstraint("sample_count >= 0 AND total >= 0", name="ck_workout_diagnostic_aggregate_counts"),
    )
