import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


class ExerciseRevision(Base):
    __tablename__ = "exercise_revisions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    exercise_id = Column(String, ForeignKey("exercises.id", ondelete="RESTRICT"), nullable=False, index=True)
    content_hash = Column(String(64), nullable=False)
    payload = Column(JSONB, nullable=False)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    submitted_at = Column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("exercise_id", "content_hash"),)


class ExerciseReview(Base):
    __tablename__ = "exercise_reviews"
    id = Column(Integer, primary_key=True, autoincrement=True)
    revision_id = Column(String, ForeignKey("exercise_revisions.id", ondelete="RESTRICT"), nullable=False, index=True)
    content_hash = Column(String(64), nullable=False)
    reviewer = Column(String, nullable=False)
    role = Column(String, nullable=False)
    decision = Column(String, nullable=False)
    comments = Column(Text, nullable=False, default="")
    artifact_hash = Column(String(64), unique=True)
    reviewed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ExercisePublication(Base):
    __tablename__ = "exercise_publications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    exercise_id = Column(String, ForeignKey("exercises.id", ondelete="RESTRICT"), nullable=False)
    revision_id = Column(String, ForeignKey("exercise_revisions.id", ondelete="RESTRICT"), nullable=False)
    operator = Column(String, nullable=False)
    release_id = Column(String, nullable=False)
    action = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ExerciseMediaAsset(Base):
    __tablename__ = "exercise_media_assets"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    exercise_id = Column(String, ForeignKey("exercises.id", ondelete="RESTRICT"), nullable=False, index=True)
    content_hash = Column(String(64), nullable=False)
    storage_key = Column(String, nullable=False)
    poster_key = Column(String)
    mime_type = Column(String, nullable=False, default="video/mp4")
    metadata_json = Column(JSONB, nullable=False, default=dict)
    production_method = Column(String, nullable=False)
    rights_documentation = Column(Text)
    technical_validated = Column(Boolean, nullable=False, default=False)
    approval_hash = Column(String(64), nullable=False)
    trainer_reviewer = Column(String)
    publication_reviewer = Column(String)
    approved_at = Column(DateTime(timezone=True))
    published_url = Column(String)
    poster_url = Column(String)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("exercise_id", "approval_hash"),)


class ExerciseMediaPublication(Base):
    __tablename__ = "exercise_media_publications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_id = Column(String, ForeignKey("exercise_media_assets.id", ondelete="RESTRICT"), nullable=False)
    operator = Column(String, nullable=False)
    release_id = Column(String, nullable=False)
    action = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ExerciseMediaReview(Base):
    __tablename__ = "exercise_media_reviews"
    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_id = Column(String, ForeignKey("exercise_media_assets.id", ondelete="RESTRICT"), nullable=False, index=True)
    approval_hash = Column(String(64), nullable=False)
    reviewer = Column(String, nullable=False)
    role = Column(String, nullable=False)
    decision = Column(String, nullable=False)
    comments = Column(Text, nullable=False, default="")
    artifact_hash = Column(String(64), unique=True)
    reviewed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
