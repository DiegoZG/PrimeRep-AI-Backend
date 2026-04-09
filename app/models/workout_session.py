import uuid

from sqlalchemy import Column, Date, DateTime, ForeignKey, Index, String, func
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
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user = relationship("User", foreign_keys=[user_id])
    set_logs = relationship("SetLog", back_populates="session", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_workout_sessions_user_id", "user_id"),
        Index("ix_workout_sessions_user_date", "user_id", "workout_date"),
    )
