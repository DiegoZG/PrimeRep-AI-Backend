from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint

from app.core.database import Base


class FeatureFlagConfig(Base):
    __tablename__ = "feature_flag_configs"

    environment = Column(String(32), primary_key=True)
    version = Column(Integer, nullable=False)
    subscriptions_purchases_enabled = Column(Boolean, nullable=False, default=False)
    subscriptions_enforcement_enabled = Column(Boolean, nullable=False, default=False)
    subscriptions_launch_at = Column(DateTime(timezone=True), nullable=True)
    launch_locked = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_feature_flag_version_positive"),
        CheckConstraint(
            "NOT subscriptions_enforcement_enabled OR (subscriptions_purchases_enabled AND subscriptions_launch_at IS NOT NULL AND launch_locked)",
            name="ck_feature_flag_enforcement_ready",
        ),
        CheckConstraint("NOT launch_locked OR subscriptions_launch_at IS NOT NULL", name="ck_feature_flag_locked_launch"),
    )


class FeatureFlagAudit(Base):
    __tablename__ = "feature_flag_audits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    environment = Column(String(32), ForeignKey("feature_flag_configs.environment"), nullable=False)
    version = Column(Integer, nullable=False)
    changed_at = Column(DateTime(timezone=True), nullable=False)
    changed_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    before = Column(JSON, nullable=False)
    after = Column(JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint("environment", "version", name="uq_feature_flag_audit_version"),
    )
