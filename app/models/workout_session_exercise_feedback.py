import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func

from app.core.database import Base


class WorkoutSessionExerciseFeedback(Base):
    __tablename__ = "workout_session_exercise_feedback"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("workout_sessions.id", ondelete="CASCADE"), nullable=False)
    exercise_id = Column(String, ForeignKey("exercises.id"), nullable=False)
    effort = Column(String, nullable=True)
    rir = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("session_id", "exercise_id", name="uq_workout_session_feedback_session_exercise"),
        CheckConstraint("effort IS NULL OR effort IN ('easy', 'right', 'hard')", name="ck_workout_session_feedback_effort"),
        CheckConstraint("rir IS NULL OR rir BETWEEN 0 AND 5", name="ck_workout_session_feedback_rir"),
    )
