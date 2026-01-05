from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


exercise_equipment = Table(
    "exercise_equipment",
    Base.metadata,
    Column(
        "exercise_id",
        String,
        ForeignKey("exercises.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "equipment_id",
        String,
        ForeignKey("equipment.id", ondelete="RESTRICT"),
        primary_key=True,
        nullable=False,
    ),
)


user_exercise_favorites = Table(
    "user_exercise_favorites",
    Base.metadata,
    Column(
        "user_id",
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "exercise_id",
        String,
        ForeignKey("exercises.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
)


class Exercise(Base):
    __tablename__ = "exercises"

    id = Column(String, primary_key=True, nullable=False)
    name = Column(String, nullable=False)
    exercise_type = Column(String, nullable=False)
    primary_muscle = Column(String, nullable=False)
    secondary_muscles = Column(JSONB, nullable=False, default=list)
    demo_video_url = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    source = Column(String, nullable=False, server_default=text("'seed'"))
    owner_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    equipment = relationship(
        "Equipment",
        secondary=exercise_equipment,
        lazy="selectin",
    )
    owner = relationship("User", foreign_keys=[owner_user_id])


