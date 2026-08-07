import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class ExerciseQuestion(Base):
    __tablename__ = "exercise_questions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    exercise_id = Column(
        String,
        ForeignKey("exercises.id"),
        nullable=False,
    )
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user = relationship("User", foreign_keys=[user_id])
    exercise = relationship("Exercise", foreign_keys=[exercise_id])

    __table_args__ = (
        # Rate-limit lookups: "how many questions has this user asked today?"
        Index("ix_exercise_questions_user_created", "user_id", "created_at"),
        # History lookups: "this user's Q&A for this exercise"
        Index("ix_exercise_questions_user_exercise", "user_id", "exercise_id"),
    )
