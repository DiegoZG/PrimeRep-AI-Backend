import uuid

from datetime import datetime, timezone

from sqlalchemy import Column, Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class WorkoutSession(Base):
    __tablename__ = "workout_sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Soft reference — workoutDayId from the plan JSON, not a DB FK
    workout_day_id = Column(String, nullable=False)
    workout_date = Column(Date, nullable=False)
    day_type = Column(String, nullable=False)
    # Immutable copy of the generated workout. Weekly plans may be skipped or
    # regenerated after a session starts, but the active session must remain
    # renderable and resumable.
    workout_snapshot = Column(JSONB, nullable=True)
    client_session_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default="in_progress")
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    workout_note = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user = relationship("User", foreign_keys=[user_id])
    set_logs = relationship(
        "SetLog",
        primaryjoin="and_(WorkoutSession.id == SetLog.session_id, SetLog.deleted_at.is_(None))",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    deleted_set_logs = relationship(
        "SetLog",
        primaryjoin="and_(WorkoutSession.id == SetLog.session_id, SetLog.deleted_at.isnot(None))",
        viewonly=True,
    )
    exercise_feedback = relationship("WorkoutSessionExerciseFeedback", cascade="all, delete-orphan")

    @property
    def recovery_required(self) -> bool:
        return self.status == "in_progress" and self.workout_snapshot is None

    @property
    def summary(self) -> dict:
        snapshot = self.workout_snapshot or {}
        title = snapshot.get("title") or self.day_type.replace("_", " ").title()
        elapsed_end = self.completed_at or datetime.now(timezone.utc)
        duration_seconds = max(0, int((elapsed_end - self.started_at).total_seconds()))
        total_volume_kg = sum(
            float(set_log.weight_kg or 0) * set_log.reps
            for set_log in self.set_logs
        )
        return {
            "title": title,
            "duration_seconds": duration_seconds,
            "completed_set_count": len(self.set_logs),
            "total_volume_kg": round(total_volume_kg, 2),
        }

    __table_args__ = (
        Index("ix_workout_sessions_user_id", "user_id"),
        Index("ix_workout_sessions_user_date", "user_id", "workout_date"),
        Index("ix_workout_sessions_user_status", "user_id", "status"),
        # Client IDs are generated per installation, not globally. Scope their
        # idempotency guarantee to their owner and enforce one resumable session.
        UniqueConstraint("user_id", "client_session_id", name="uq_workout_sessions_user_client_session"),
        Index(
            "uq_workout_sessions_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'in_progress'"),
        ),
    )
