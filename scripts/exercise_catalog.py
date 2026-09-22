"""Trusted operator workflow for immutable, reviewed exercise content.

Import batches of at most 25. Review identities are supplied by actual reviewers;
this command never infers approval from a filename or an AI output.
"""
import argparse
import difflib
import html
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func

from app.core.database import SessionLocal
from app.core.exercise_catalog_service import (
    catalog_version, import_entry, publish_revision, review_revision, submit_revision,
    validate_entry, review_media, content_hash, locked_revision, _locked_media,
)
from app.models.exercise import Exercise
from app.models.exercise_catalog import ExerciseMediaAsset, ExerciseMediaReview, ExercisePublication, ExerciseReview, ExerciseRevision
from app.schemas.exercise_catalog import CatalogManifest, ReviewArtifact


def import_review(db, artifact: ReviewArtifact):
    digest = content_hash(artifact.model_dump(mode="json"))
    if artifact.subject_type == "content":
        locked_revision(db, artifact.subject_id, artifact.subject_hash)
        model = ExerciseReview
    else:
        _locked_media(db, artifact.subject_id, artifact.subject_hash)
        model = ExerciseMediaReview
    existing = db.query(model).filter_by(artifact_hash=digest).first()
    if existing:
        return {"review_id": existing.id, "artifact_hash": digest, "already_imported": True}
    arguments = dict(reviewer=artifact.reviewer, role=artifact.role, decision=artifact.decision,
                     comments=artifact.comments, artifact_hash=digest, reviewed_at=artifact.reviewed_at)
    if artifact.subject_type == "content":
        review = review_revision(db, artifact.subject_id, artifact.subject_hash, **arguments)
    else:
        review_media(db, artifact.subject_id, artifact.subject_hash, **arguments)
        review = db.query(model).filter_by(artifact_hash=digest).one()
    return {"review_id": review.id, "artifact_hash": digest, "already_imported": False}


def report(db):
    revisions = db.query(ExerciseRevision).all()
    statuses = dict(db.query(Exercise.publication_status, func.count()).filter(Exercise.source.in_(["seed", "catalog"])).group_by(Exercise.publication_status).all())
    pending = []
    for revision in revisions:
        latest = {}
        for review in db.query(ExerciseReview).filter_by(revision_id=revision.id).order_by(ExerciseReview.id.desc()):
            latest.setdefault(review.role, review.decision)
        pending.append({"exercise_id": revision.exercise_id, "revision_id": revision.id,
                        "content_hash": revision.content_hash, "submitted": revision.submitted_at is not None,
                        "trainer": latest.get("trainer", "pending"), "publisher": latest.get("publisher", "pending")})
    return {"catalog_version": catalog_version(db), "canonical_by_status": statuses,
            "private_count": db.query(Exercise).filter_by(source="user").count(),
            "media_staged": db.query(ExerciseMediaAsset).filter(ExerciseMediaAsset.published_url.is_(None)).count(),
            "media_published": db.query(Exercise).filter(Exercise.published_media.is_not(None), Exercise.published_media != "null").count(),
            "revisions": pending}


def review_packet(db, revision_id: str) -> str:
    revision = db.get(ExerciseRevision, revision_id)
    if not revision:
        raise ValueError("Revision not found")
    exercise = db.get(Exercise, revision.exercise_id)
    prior = db.get(ExerciseRevision, exercise.published_revision_id) if exercise.published_revision_id else None
    diff = "\n".join(difflib.unified_diff(
        json.dumps(prior.payload if prior else exercise.legacy_snapshot or {"legacy": {key: getattr(exercise, key) for key in ("how_to", "common_mistakes", "why_it_works", "beginner_notes")}}, indent=2, ensure_ascii=False).splitlines(),
        json.dumps(revision.payload, indent=2, ensure_ascii=False).splitlines(),
        fromfile="published", tofile="submitted"))
    payload = revision.payload
    sections = [f"<h1>{html.escape(payload['name'])}</h1>", f"<p>Revision: {html.escape(revision.id)}<br>Hash: {html.escape(revision.content_hash)}</p>",
                "<p>Trainer and publication approvals are pending unless recorded separately for this exact hash.</p>"]
    for title, value in payload.items():
        if title == "sources":
            sections.append("<h2>Research references (not media licenses)</h2><ul>" + "".join(
                f'<li><a href="{html.escape(source["url"], quote=True)}">{html.escape(source["title"])}</a> — accessed {html.escape(source["accessed_at"])}<p>{html.escape("; ".join(source["claims"]))}</p></li>' for source in value) + "</ul>")
        else:
            sections.append(f"<h2>{html.escape(title.replace('_', ' ').title())}</h2><pre>{html.escape(json.dumps(value, indent=2, ensure_ascii=False))}</pre>")
    assets = [{"id": asset.id, "approval_hash": asset.approval_hash, "method": asset.production_method, "technical_validated": asset.technical_validated, "trainer": asset.trainer_reviewer, "publisher": asset.publication_reviewer} for asset in db.query(ExerciseMediaAsset).filter_by(exercise_id=exercise.id)]
    sections += ["<h2>Associated media review status</h2><pre>" + html.escape(json.dumps(assets, indent=2)) + "</pre>", "<h2>Changes from published content</h2><pre>" + html.escape(diff) + "</pre>"]
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Exercise review</title><style>body{max-width:900px;margin:40px auto;padding:0 20px;font:16px/1.6 system-ui}pre{white-space:pre-wrap;background:#f3f4f6;padding:16px}h2{margin-top:32px}</style><body>' + "\n".join(sections) + "</body></html>"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "import"):
        sub = commands.add_parser(command)
        sub.add_argument("file", type=Path)
        sub.add_argument("--operator", required=command == "import")
        sub.add_argument("--dry-run", action="store_true")
    for command in ("submit", "review", "publish", "rollback"):
        sub = commands.add_parser(command)
        sub.add_argument("revision_id")
        sub.add_argument("--hash", required=True)
        sub.add_argument("--dry-run", action="store_true")
        if command == "review":
            sub.add_argument("--reviewer", required=True)
            sub.add_argument("--role", choices=["trainer", "publisher"], required=True)
            sub.add_argument("--decision", choices=["approved", "rejected"], required=True)
            sub.add_argument("--comments", default="")
        elif command in {"publish", "rollback"}:
            sub.add_argument("--operator", required=True)
            sub.add_argument("--release-id", required=True)
    commands.add_parser("report")
    review_import = commands.add_parser("review-import")
    review_import.add_argument("file", type=Path)
    review_import.add_argument("--dry-run", action="store_true")
    media_review = commands.add_parser("media-review")
    media_review.add_argument("asset_id")
    media_review.add_argument("--hash", required=True)
    media_review.add_argument("--reviewer", required=True)
    media_review.add_argument("--role", choices=["trainer", "publisher"], required=True)
    media_review.add_argument("--decision", choices=["approved", "rejected"], required=True)
    media_review.add_argument("--comments", default="")
    media_review.add_argument("--dry-run", action="store_true")
    packet = commands.add_parser("review-packet")
    packet.add_argument("revision_id")
    packet.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with SessionLocal() as db:
        try:
            if args.command in {"validate", "import"}:
                manifest = CatalogManifest.model_validate_json(args.file.read_text())
                if args.command == "import" and len(manifest.exercises) > 25:
                    raise ValueError("Import review batches of at most 25 exercises")
                result = []
                for entry in manifest.exercises:
                    validate_entry(db, entry)
                    if args.command == "import":
                        revision = import_entry(db, entry, args.operator)
                        result.append({"id": entry.id, "revision_id": revision.id, "content_hash": revision.content_hash})
                if args.command == "validate":
                    result = {"valid_exercises": len(manifest.exercises)}
            elif args.command == "submit":
                revision = submit_revision(db, args.revision_id, args.hash)
                result = {"revision_id": revision.id, "submitted": True}
            elif args.command == "review":
                review = review_revision(db, args.revision_id, args.hash, reviewer=args.reviewer, role=args.role, decision=args.decision, comments=args.comments)
                result = {"review_id": review.id, "decision": review.decision}
            elif args.command in {"publish", "rollback"}:
                exercise = publish_revision(db, args.revision_id, args.hash, operator=args.operator, release_id=args.release_id, rollback=args.command == "rollback")
                result = {"exercise_id": exercise.id, "content_version": exercise.content_version}
            elif args.command == "review-import":
                result = import_review(db, ReviewArtifact.model_validate_json(args.file.read_text()))
            elif args.command == "media-review":
                asset = review_media(db, args.asset_id, args.hash, reviewer=args.reviewer,
                                     role=args.role, decision=args.decision, comments=args.comments)
                result = {"asset_id": asset.id, "approval_hash": asset.approval_hash,
                          "decision": args.decision, "publication_approved": asset.approved_at is not None}
            elif args.command == "report":
                result = report(db)
            else:
                args.output.write_text(review_packet(db, args.revision_id))
                result = {"output": str(args.output)}
            if getattr(args, "dry_run", False):
                db.rollback()
            else:
                db.commit()
            print(json.dumps(result, indent=2))
        except Exception:
            db.rollback()
            logging.getLogger("primerep.catalog").error("catalog_operation_failed", extra={"operation": args.command})
            raise


if __name__ == "__main__":
    main()
