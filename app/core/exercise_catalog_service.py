from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.equipment import Equipment
from app.models.exercise import Exercise
from app.models.exercise_catalog import ExerciseMediaAsset, ExerciseMediaPublication, ExerciseMediaReview, ExercisePublication, ExerciseReview, ExerciseRevision
from app.models.workout_template import ExerciseAlias
from app.schemas.exercise_catalog import CatalogEntry


def content_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _identity(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("An explicit operator/reviewer identity is required")
    return value.strip()


def catalog_version(db: Session) -> str:
    return f"{db.query(func.max(ExercisePublication.id)).scalar() or 0}.{db.query(func.max(ExerciseMediaPublication.id)).scalar() or 0}"


def validate_entry(db: Session, entry: CatalogEntry) -> None:
    valid = {row.id for row in db.query(Equipment).filter(Equipment.id.in_(entry.equipment_ids), Equipment.is_active.is_(True))}
    if valid != set(entry.equipment_ids):
        raise ValueError(f"Unknown/inactive equipment: {sorted(set(entry.equipment_ids) - valid)}")
    existing = db.get(Exercise, entry.id)
    if existing and (existing.source not in {"seed", "catalog"} or existing.owner_user_id):
        raise ValueError("Catalog imports cannot replace private or noncanonical exercises")


def import_entry(db: Session, entry: CatalogEntry, operator: str) -> ExerciseRevision:
    operator = _identity(operator)
    validate_entry(db, entry)
    payload = entry.model_dump(mode="json")
    digest = content_hash(payload)
    existing = db.query(ExerciseRevision).filter_by(exercise_id=entry.id, content_hash=digest).first()
    if existing:
        return existing
    exercise = db.get(Exercise, entry.id)
    if exercise is None:
        exercise = Exercise(id=entry.id, name=entry.name, exercise_type=entry.exercise_type,
                            primary_muscle=entry.primary_muscle, secondary_muscles=[], source="catalog",
                            publication_status="draft", generation_eligible=False, is_active=False)
        db.add(exercise)
        db.flush()
    revision = ExerciseRevision(exercise_id=entry.id, payload=payload, content_hash=digest, created_by=operator)
    db.add(revision)
    db.flush()
    return revision


def locked_revision(db: Session, revision_id: str, expected_hash: str) -> ExerciseRevision:
    revision = db.query(ExerciseRevision).filter_by(id=revision_id).with_for_update().one_or_none()
    if not revision or revision.content_hash != expected_hash or content_hash(revision.payload) != expected_hash:
        raise ValueError("Revision not found or hash mismatch")
    return revision


def submit_revision(db: Session, revision_id: str, expected_hash: str) -> ExerciseRevision:
    revision = locked_revision(db, revision_id, expected_hash)
    validate_entry(db, CatalogEntry.model_validate(revision.payload))
    if revision.submitted_at is None:
        revision.submitted_at = datetime.now(timezone.utc)
    db.flush()
    return revision


def review_revision(db: Session, revision_id: str, expected_hash: str, *, reviewer: str, role: str, decision: str, comments: str = "", artifact_hash: str | None = None, reviewed_at: datetime | None = None) -> ExerciseReview:
    revision = locked_revision(db, revision_id, expected_hash)
    if revision.submitted_at is None:
        raise ValueError("Submit the revision before review")
    if role not in {"trainer", "publisher"} or decision not in {"approved", "rejected"}:
        raise ValueError("Invalid review role or decision")
    reviewer = _identity(reviewer)
    if role == "publisher" and decision == "approved":
        trainer = db.query(ExerciseReview).filter_by(revision_id=revision_id, role="trainer").order_by(ExerciseReview.id.desc()).first()
        if not trainer or trainer.decision != "approved":
            raise ValueError("Trainer approval required before publication review")
    other = db.query(ExerciseReview).filter(ExerciseReview.revision_id == revision_id, ExerciseReview.role != role, ExerciseReview.reviewer == reviewer).first()
    if other:
        raise ValueError("Trainer and publisher must be different people")
    review = ExerciseReview(revision_id=revision_id, content_hash=expected_hash, reviewer=reviewer, role=role, decision=decision, comments=comments, artifact_hash=artifact_hash, reviewed_at=reviewed_at)
    db.add(review)
    db.flush()
    return review


def _require_approvals(db: Session, revision: ExerciseRevision):
    reviews = db.query(ExerciseReview).filter_by(revision_id=revision.id, content_hash=revision.content_hash).order_by(ExerciseReview.id.desc()).all()
    latest = {}
    for review in reviews:
        latest.setdefault(review.role, review)
    if set(latest) != {"trainer", "publisher"} or any(r.decision != "approved" for r in latest.values()):
        raise ValueError("Trainer and publisher approval required for this exact revision")
    if latest["trainer"].reviewer == latest["publisher"].reviewer:
        raise ValueError("Independent trainer and publisher approval required")
    if latest["publisher"].id < latest["trainer"].id:
        raise ValueError("Publication approval required after the latest trainer review")


def publish_revision(db: Session, revision_id: str, expected_hash: str, *, operator: str, release_id: str, rollback: bool = False) -> Exercise:
    operator, release_id = _identity(operator), _identity(release_id)
    revision = locked_revision(db, revision_id, expected_hash)
    exercise = db.query(Exercise).filter_by(id=revision.exercise_id).with_for_update().one()
    if revision.submitted_at is None:
        raise ValueError("Revision has not been submitted")
    _require_approvals(db, revision)
    if rollback and not db.query(ExercisePublication.id).filter_by(revision_id=revision.id).first():
        raise ValueError("Rollback requires a previously published revision")
    entry = CatalogEntry.model_validate(revision.payload)
    validate_entry(db, entry)
    for field in ("name", "exercise_type", "primary_muscle", "secondary_muscles", "movement_pattern", "resistance_modality", "difficulty", "laterality", "tracking_mode", "generation_eligible"):
        setattr(exercise, field, getattr(entry, field))
    exercise.load_profile = entry.load_profile.model_dump()
    exercise.structured_content = entry.structured_content.model_dump()
    exercise.how_to = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(entry.structured_content.steps))
    exercise.why_it_works = entry.structured_content.benefits
    exercise.common_mistakes = "\n".join(entry.structured_content.mistakes)
    exercise.beginner_notes = entry.structured_content.beginner_guidance
    exercise.equipment = db.query(Equipment).filter(Equipment.id.in_(entry.equipment_ids)).all()
    exercise.content_version = revision.content_hash
    exercise.published_revision_id = revision.id
    exercise.publication_status = "published"
    exercise.is_active = True
    db.query(ExerciseAlias).filter_by(exercise_id=exercise.id).delete(synchronize_session=False)
    for alias in entry.aliases:
        db.add(ExerciseAlias(exercise_id=exercise.id, alias=alias.strip().lower()))
    db.add(ExercisePublication(exercise_id=exercise.id, revision_id=revision.id, operator=operator, release_id=release_id, action="rollback" if rollback else "publish"))
    from app.core.workout_template_service import _bump_referencing_template_versions
    _bump_referencing_template_versions(db, None, exercise.id)
    db.flush()
    return exercise


MEDIA_REVIEW_FIELDS = ("exercise_id", "content_hash", "storage_key", "poster_key", "mime_type", "metadata_json", "production_method", "rights_documentation", "technical_validated")


def register_media(db: Session, **values) -> ExerciseMediaAsset:
    import re
    if set(values) != set(MEDIA_REVIEW_FIELDS):
        raise ValueError("Supply exactly the media submission fields")
    exercise = db.get(Exercise, values["exercise_id"])
    if not exercise or exercise.source not in {"seed", "catalog"} or exercise.owner_user_id:
        raise ValueError("Canonical exercise required")
    if not re.fullmatch(r"[a-f0-9]{64}", values["content_hash"]):
        raise ValueError("SHA-256 content hash required")
    for key in (values["storage_key"], values["poster_key"]):
        if key and (key.startswith("/") or ".." in key.split("/") or ":" in key or "?" in key or "#" in key):
            raise ValueError("Relative immutable storage keys required")
    if values["mime_type"] != "video/mp4" or values["production_method"] not in {"filmed", "ai_generated", "hybrid"}:
        raise ValueError("Unsupported media type or production method")
    digest = content_hash(values)
    existing = db.query(ExerciseMediaAsset).filter_by(exercise_id=values["exercise_id"], approval_hash=digest).first()
    if existing:
        return existing
    asset = ExerciseMediaAsset(**values, approval_hash=digest)
    db.add(asset)
    db.flush()
    return asset


def _locked_media(db: Session, asset_id: str, expected_hash: str) -> ExerciseMediaAsset:
    asset = db.query(ExerciseMediaAsset).filter_by(id=asset_id).with_for_update().one_or_none()
    if not asset or asset.approval_hash != expected_hash or content_hash({key: getattr(asset, key) for key in MEDIA_REVIEW_FIELDS}) != expected_hash:
        raise ValueError("Media not found or approval hash mismatch")
    return asset


def media_review_hash(asset: ExerciseMediaAsset) -> str:
    return content_hash({key: getattr(asset, key) for key in MEDIA_REVIEW_FIELDS})


def validate_media_publication(asset: ExerciseMediaAsset, expected_hash: str) -> None:
    if asset.approval_hash != expected_hash or media_review_hash(asset) != expected_hash:
        raise ValueError("Media approval hash mismatch")
    if not asset.technical_validated or not (asset.rights_documentation or "").strip() or not asset.approved_at or not asset.trainer_reviewer or not asset.publication_reviewer or asset.trainer_reviewer == asset.publication_reviewer:
        raise ValueError("Technical validation, rights, and both independent media approvals required")


def review_media(db: Session, asset_id: str, expected_hash: str, *, reviewer: str, role: str, decision: str = "approved", comments: str = "", artifact_hash: str | None = None, reviewed_at: datetime | None = None) -> ExerciseMediaAsset:
    asset = _locked_media(db, asset_id, expected_hash)
    reviewer = _identity(reviewer)
    if decision not in {"approved", "rejected"}:
        raise ValueError("Decision must be approved or rejected")
    if decision == "approved" and (not asset.technical_validated or not (asset.rights_documentation or "").strip()):
        raise ValueError("Technical validation and rights documentation required")
    if role == "trainer":
        if asset.publication_reviewer == reviewer:
            raise ValueError("Independent reviewers required")
        asset.trainer_reviewer = reviewer if decision == "approved" else None
        asset.publication_reviewer = None
        asset.approved_at = None
    elif role == "publisher":
        if decision == "approved" and (not asset.trainer_reviewer or asset.trainer_reviewer == reviewer):
            raise ValueError("Independent trainer review required first")
        asset.publication_reviewer = reviewer if decision == "approved" else None
        asset.approved_at = datetime.now(timezone.utc) if decision == "approved" else None
    else:
        raise ValueError("Role must be trainer or publisher")
    db.add(ExerciseMediaReview(asset_id=asset.id, approval_hash=expected_hash, reviewer=reviewer, role=role, decision=decision, comments=comments, artifact_hash=artifact_hash, reviewed_at=reviewed_at))
    if not asset.approved_at:
        exercise = db.query(Exercise).filter_by(id=asset.exercise_id).with_for_update().one()
        if (exercise.published_media or {}).get("id") == asset.id:
            exercise.published_media = None
            exercise.demo_video_url = exercise.image_url = None
            db.add(ExerciseMediaPublication(asset_id=asset.id, operator=reviewer, release_id="review-withdrawal", action="withdraw"))
    db.flush()
    return asset


def publish_media(db: Session, asset_id: str, expected_hash: str, *, public_base_url: str, operator: str, release_id: str, rollback: bool = False) -> ExerciseMediaAsset:
    from urllib.parse import urlparse
    asset = _locked_media(db, asset_id, expected_hash)
    validate_media_publication(asset, expected_hash)
    preflight_media_publication(db, asset.id, expected_hash, rollback=rollback)
    operator, release_id = _identity(operator), _identity(release_id)
    if rollback and not db.query(ExerciseMediaPublication.id).filter_by(asset_id=asset.id).first():
        raise ValueError("Rollback requires previously published media")
    url = urlparse(public_base_url)
    if url.scheme != "https" or not url.netloc or url.query or url.fragment or url.username:
        raise ValueError("Trusted HTTPS public media base required")
    exercise = db.query(Exercise).filter_by(id=asset.exercise_id).with_for_update().one()
    if exercise.publication_status not in {"legacy", "published"} or not exercise.is_active:
        raise ValueError("Publish exercise content before its media")
    asset.published_url = public_base_url.rstrip("/") + "/" + asset.storage_key
    asset.poster_url = public_base_url.rstrip("/") + "/" + asset.poster_key if asset.poster_key else None
    exercise.published_media = {"id": asset.id, "url": asset.published_url, "mime_type": asset.mime_type, "poster_url": asset.poster_url}
    exercise.demo_video_url, exercise.image_url = asset.published_url, asset.poster_url
    db.add(ExerciseMediaPublication(asset_id=asset.id, operator=operator, release_id=release_id, action="rollback" if rollback else "publish"))
    db.flush()
    return asset


def preflight_media_publication(db: Session, asset_id: str, expected_hash: str, *, rollback: bool = False) -> ExerciseMediaAsset:
    asset = _locked_media(db, asset_id, expected_hash)
    validate_media_publication(asset, expected_hash)
    latest = {}
    for review in db.query(ExerciseMediaReview).filter_by(asset_id=asset.id, approval_hash=expected_hash).order_by(ExerciseMediaReview.id.desc()):
        latest.setdefault(review.role, review)
    if set(latest) != {"trainer", "publisher"} or any(row.decision != "approved" for row in latest.values()) or latest["publisher"].id < latest["trainer"].id:
        raise ValueError("Current trainer and publication reviews required")
    if rollback and not db.query(ExerciseMediaPublication.id).filter_by(asset_id=asset.id).first():
        raise ValueError("Rollback requires previously published media")
    exercise = db.query(Exercise).filter_by(id=asset.exercise_id).with_for_update().one()
    if exercise.publication_status not in {"legacy", "published"} or not exercise.is_active:
        raise ValueError("Publish exercise content before its media")
    return asset
