import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.orm import relationship

from app.core.database import Base


class SetLog(Base):
    __tablename__ = "set_logs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(
        String,
        ForeignKey("workout_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    exercise_id = Column(
        String,
        ForeignKey("exercises.id"),
        nullable=False,
    )
    set_number = Column(Integer, nullable=False)
    reps = Column(Integer, nullable=False)
    # Null for bodyweight exercises
    weight_kg = Column(Float, nullable=True)
    client_operation_id = Column(String, nullable=False)
    version = Column(Integer, nullable=False, server_default="1")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    logged_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    session = relationship("WorkoutSession", back_populates="set_logs")
    exercise = relationship("Exercise", foreign_keys=[exercise_id])

    __table_args__ = (
        Index("ix_set_logs_session_id", "session_id"),
        Index("ix_set_logs_exercise_id", "exercise_id"),
        Index(
            "ix_set_logs_active_slot",
            "session_id",
            "exercise_id",
            "set_number",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        UniqueConstraint("session_id", "client_operation_id", name="uq_set_logs_session_client_operation"),
    )
