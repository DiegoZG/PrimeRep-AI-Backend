from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String
from sqlalchemy.orm import relationship

from app.core.database import Base


class PushToken(Base):
    __tablename__ = "push_tokens"
    __table_args__ = (
        CheckConstraint("platform IN ('ios', 'android')", name="ck_push_tokens_platform"),
    )

    token = Column(String, primary_key=True)
    user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform = Column(String, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user = relationship("User", back_populates="push_tokens")
