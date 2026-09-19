import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class WorkoutTemplate(Base):
    __tablename__ = "workout_templates"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    scope = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=False, default="")
    goal = Column(String, nullable=False)
    level = Column(String, nullable=False)
    frequency = Column(Integer, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    featured_rank = Column(Integer, nullable=True)
    version = Column(Integer, nullable=False, default=1, server_default="1")
    is_archived = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    owner = relationship("User", foreign_keys=[owner_user_id])
    days = relationship(
        "WorkoutTemplateDay",
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="WorkoutTemplateDay.position",
    )
    aliases = relationship(
        "WorkoutTemplateAlias", back_populates="template", cascade="all, delete-orphan"
    )
    equipment_requirements = relationship(
        "WorkoutTemplateEquipment", back_populates="template", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("scope IN ('system', 'private')", name="ck_workout_templates_scope"),
        CheckConstraint(
            "goal IN ('build-muscle', 'get-stronger', 'general-fitness', 'conditioning')",
            name="ck_workout_templates_goal",
        ),
        CheckConstraint(
            "level IN ('no-experience', 'beginner', 'intermediate', 'advanced')",
            name="ck_workout_templates_level",
        ),
        CheckConstraint("frequency BETWEEN 1 AND 7", name="ck_workout_templates_frequency"),
        CheckConstraint(
            "duration_minutes BETWEEN 15 AND 120", name="ck_workout_templates_duration"
        ),
        CheckConstraint(
            "(scope = 'system' AND owner_user_id IS NULL) OR "
            "(scope = 'private' AND owner_user_id IS NOT NULL)",
            name="ck_workout_templates_owner",
        ),
        Index("ix_workout_templates_owner", "owner_user_id"),
        Index("ix_workout_templates_catalog", "scope", "is_archived", "featured_rank"),
    )


class WorkoutTemplateDay(Base):
    __tablename__ = "workout_template_days"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    template_id = Column(
        String, ForeignKey("workout_templates.id", ondelete="CASCADE"), nullable=False
    )
    position = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    day_type = Column(String, nullable=False)

    template = relationship("WorkoutTemplate", back_populates="days")
    exercises = relationship(
        "WorkoutTemplateExercise",
        back_populates="day",
        cascade="all, delete-orphan",
        order_by="WorkoutTemplateExercise.position",
    )

    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_workout_template_days_position"),
        UniqueConstraint("template_id", "position", name="uq_workout_template_days_position"),
    )


class WorkoutTemplateExercise(Base):
    __tablename__ = "workout_template_exercises"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    template_day_id = Column(
        String, ForeignKey("workout_template_days.id", ondelete="CASCADE"), nullable=False
    )
    exercise_id = Column(String, ForeignKey("exercises.id", ondelete="RESTRICT"), nullable=False)
    position = Column(Integer, nullable=False)
    block_type = Column(String, nullable=False, default="main", server_default="main")
    sets = Column(Integer, nullable=False)
    reps_min = Column(Integer, nullable=False)
    reps_max = Column(Integer, nullable=False)
    rest_seconds = Column(Integer, nullable=False)
    cue = Column(Text, nullable=True)

    day = relationship("WorkoutTemplateDay", back_populates="exercises")
    exercise = relationship("Exercise", lazy="selectin")

    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_workout_template_exercises_position"),
        CheckConstraint("sets BETWEEN 1 AND 20", name="ck_workout_template_exercises_sets"),
        CheckConstraint(
            "reps_min >= 1 AND reps_max >= reps_min AND reps_max <= 100",
            name="ck_workout_template_exercises_reps",
        ),
        CheckConstraint(
            "rest_seconds BETWEEN 0 AND 900", name="ck_workout_template_exercises_rest"
        ),
        UniqueConstraint(
            "template_day_id", "position", name="uq_workout_template_exercises_position"
        ),
        UniqueConstraint(
            "template_day_id", "exercise_id", name="uq_workout_template_exercises_exercise"
        ),
    )


class WorkoutTemplateEquipment(Base):
    __tablename__ = "workout_template_equipment"

    template_id = Column(
        String, ForeignKey("workout_templates.id", ondelete="CASCADE"), primary_key=True
    )
    equipment_id = Column(
        String, ForeignKey("equipment.id", ondelete="RESTRICT"), primary_key=True
    )
    template = relationship("WorkoutTemplate", back_populates="equipment_requirements")
    equipment = relationship("Equipment", lazy="joined")


class WorkoutTemplateAlias(Base):
    __tablename__ = "workout_template_aliases"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    template_id = Column(
        String, ForeignKey("workout_templates.id", ondelete="CASCADE"), nullable=False
    )
    alias = Column(String, nullable=False)
    template = relationship("WorkoutTemplate", back_populates="aliases")

    __table_args__ = (
        UniqueConstraint("template_id", "alias", name="uq_workout_template_alias"),
    )


class ExerciseAlias(Base):
    __tablename__ = "exercise_aliases"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    exercise_id = Column(String, ForeignKey("exercises.id", ondelete="CASCADE"), nullable=False)
    alias = Column(String, nullable=False)

    __table_args__ = (UniqueConstraint("exercise_id", "alias", name="uq_exercise_alias"),)


class UserProgramActivation(Base):
    __tablename__ = "user_program_activations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    template_id = Column(
        String, ForeignKey("workout_templates.id", ondelete="SET NULL"), nullable=True
    )
    template_version = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="active", server_default="active")
    effective_date = Column(Date, nullable=False)
    weekdays = Column(JSONB, nullable=False)
    activation_snapshot = Column(JSONB, nullable=False)
    apply_mode = Column(String, nullable=False)
    client_operation_id = Column(String, nullable=False)
    request_fingerprint = Column(String, nullable=False)
    requires_review = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deactivated_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    template = relationship("WorkoutTemplate", foreign_keys=[template_id])

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'scheduled', 'superseded', 'deactivated')",
            name="ck_user_program_activations_status",
        ),
        CheckConstraint(
            "apply_mode IN ('now', 'next-week')", name="ck_user_program_activations_apply_mode"
        ),
        UniqueConstraint(
            "user_id", "client_operation_id", name="uq_user_program_activation_operation"
        ),
        Index("ix_user_program_activations_user", "user_id", "status"),
        Index(
            "uq_user_program_activations_one_active",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_user_program_activations_one_scheduled",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'scheduled'"),
        ),
    )
