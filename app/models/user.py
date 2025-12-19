import uuid
from sqlalchemy import Column, String, DateTime, Boolean, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, nullable=False, index=True)

    preferred_name = Column(String, nullable=False)
    last_name = Column(String, nullable=True)

    password_hash = Column(String, nullable=False)

    has_completed_onboarding = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    onboarding_profile = relationship(
        "OnboardingProfile",
        uselist=False,
        back_populates="user",
    )
