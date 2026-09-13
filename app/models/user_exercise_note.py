import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, UniqueConstraint, func

from app.core.database import Base


class UserExerciseNote(Base):
    __tablename__ = "user_exercise_notes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    exercise_id = Column(String, ForeignKey("exercises.id"), nullable=False)
    note = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "exercise_id", name="uq_user_exercise_notes_user_exercise"),)
