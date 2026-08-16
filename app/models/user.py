import uuid
from sqlalchemy import Column, String, DateTime, Boolean, Integer, Date, ForeignKey, func
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

    subscription_tier = Column(String, nullable=False, default="free")
    coach_insights_enabled = Column(Boolean, nullable=False, default=False)

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
    equipment_weights = relationship(
        "UserEquipmentWeights",
        uselist=False,
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserDailyForceRegen(Base):
    __tablename__ = "user_daily_force_regens"

    user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    regen_date = Column(Date, primary_key=True, nullable=False)
    count = Column(Integer, nullable=False, default=1)
