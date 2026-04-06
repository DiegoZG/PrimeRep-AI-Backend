import uuid

from sqlalchemy import Column, Date, DateTime, ForeignKey, String, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class WorkoutDayHistory(Base):
    __tablename__ = "workout_day_history"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    workout_date = Column(Date, nullable=False)
    day_type = Column(String, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user = relationship("User", foreign_keys=[user_id])

