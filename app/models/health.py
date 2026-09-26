from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, func, text

from app.core.database import Base


class HealthSource(Base):
    __tablename__ = "health_sources"

    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    source = Column(String, primary_key=True)
    connection_revision = Column(Integer, nullable=False, server_default="1")
    enabled = Column(Boolean, nullable=False, server_default=text("false"))
    read_enabled = Column(Boolean, nullable=False, server_default=text("false"))
    export_enabled = Column(Boolean, nullable=False, server_default=text("false"))
    connected_at = Column(DateTime(timezone=True), nullable=True)
    disconnected_at = Column(DateTime(timezone=True), nullable=True)
    last_successful_sync_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("source IN ('apple_health', 'health_connect')", name="ck_health_sources_source"),
        CheckConstraint("connection_revision > 0", name="ck_health_sources_revision"),
    )


class HealthPreference(Base):
    __tablename__ = "health_preferences"

    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    primary_source = Column(String, nullable=True)
    coach_enabled = Column(Boolean, nullable=False, server_default=text("false"))

    __table_args__ = (
        CheckConstraint("primary_source IS NULL OR primary_source IN ('apple_health', 'health_connect')", name="ck_health_preferences_primary_source"),
    )


class HealthDay(Base):
    __tablename__ = "health_days"

    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    source = Column(String, primary_key=True)
    local_date = Column(Date, primary_key=True)
    time_zone = Column(String, primary_key=True)
    steps = Column(Integer, nullable=True)
    asleep_minutes = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        ForeignKeyConstraint(["user_id", "source"], ["health_sources.user_id", "health_sources.source"], ondelete="CASCADE"),
        CheckConstraint("source IN ('apple_health', 'health_connect')", name="ck_health_days_source"),
        CheckConstraint("steps IS NULL OR steps >= 0", name="ck_health_days_steps"),
        CheckConstraint("asleep_minutes IS NULL OR asleep_minutes BETWEEN 0 AND 1440", name="ck_health_days_asleep_minutes"),
    )
