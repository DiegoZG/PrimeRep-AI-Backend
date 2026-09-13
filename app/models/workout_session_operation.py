import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint, func

from app.core.database import Base


class WorkoutSessionOperation(Base):
    __tablename__ = "workout_session_operations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("workout_sessions.id", ondelete="CASCADE"), nullable=False)
    client_operation_id = Column(String, nullable=False)
    operation_type = Column(String, nullable=False)
    target_set_log_id = Column(String, ForeignKey("set_logs.id", ondelete="SET NULL"), nullable=True)
    target_exercise_id = Column(String, ForeignKey("exercises.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("session_id", "client_operation_id", name="uq_workout_session_operations_session_client"),
    )
