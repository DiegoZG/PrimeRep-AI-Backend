"""Stage original exercise footage privately; publish only reviewed derivatives.

Stage: python scripts/upload_local_footage.py stage --dir footage --operator NAME
       --production-method filmed --rights-file rights.txt --variant front-three-quarter
Publish: python scripts/upload_local_footage.py publish --asset-id ID --hash HASH
         --operator NAME --release-id RELEASE

Files are named by exercise ID. Publication approvals are recorded using
scripts/exercise_catalog.py; uploading alone never changes a visible exercise.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
from scripts.media_pipeline import prepare_video, probe_video

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


@dataclass
class StorageConfig:
    bucket: str
    cdn_base_url: str
    staging_bucket: str
    endpoint_url: str | None = None
    region: str = "auto"

    def validate(self, *, publish: bool = False):
        if not self.staging_bucket or self.staging_bucket == self.bucket:
            raise ValueError("A separate private ASSET_STAGING_BUCKET is required")
        if publish:
            parsed = urlparse(self.cdn_base_url)
            if not self.bucket or parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise ValueError("Publishing requires ASSET_BUCKET and an HTTPS media origin")
            if parsed.hostname.endswith(".r2.dev"):
                raise ValueError("Use a production custom media domain rather than r2.dev")


@dataclass
class RunReport:
    uploaded: list[str] = field(default_factory=list)
    already_in_storage: list[str] = field(default_factory=list)
    registered: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def normalize_slug(stem: str) -> str:
    value = stem.strip().lower().replace("-", "_").replace(" ", "_")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_]{1,119}", value):
        raise ValueError("Filename must be an exercise ID")
    return value


def object_exists(client, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception as exc:
        response = getattr(exc, "response", {})
        code = str(response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"} or response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
            return False
        raise


def build_s3_client(config: StorageConfig):
    import boto3
    return boto3.client("s3", endpoint_url=config.endpoint_url, region_name=config.region)


def upload_private(client, config: StorageConfig, key: str, path: Path, content_type: str, report: RunReport):
    if object_exists(client, config.staging_bucket, key):
        report.already_in_storage.append(key)
        return
    with path.open("rb") as source:
        client.put_object(Bucket=config.staging_bucket, Key=key, Body=source,
                          ContentType=content_type, CacheControl="private, no-store")
    report.uploaded.append(key)


def run(directory: Path, db, config: StorageConfig, client=None, *, operator: str,
        production_method: str, rights_documentation: str, variant: str,
        model_version: str | None = None, dry_run: bool = False,
        only: set[str] | None = None, prepare=prepare_video, probe=probe_video) -> RunReport:
    from app.core.exercise_catalog_service import register_media
    from app.models.exercise import Exercise
    config.validate()
    if not directory.is_dir():
        raise ValueError("Footage directory does not exist")
    if not operator.strip() or not variant.strip() or not rights_documentation.strip():
        raise ValueError("Operator, variant and rights documentation are required")
    if production_method not in {"filmed", "ai_generated", "hybrid"}:
        raise ValueError("Unknown production method")
    if production_method in {"ai_generated", "hybrid"} and not (model_version or "").strip():
        raise ValueError("AI/hybrid assets require a model/tool version")
    report = RunReport()
    for source in sorted(directory.iterdir()):
        if not source.is_file() or source.name.startswith("."):
            continue
        if source.suffix.lower() not in VIDEO_EXTENSIONS:
            report.warnings.append(f"{source.name}: only video masters are staged; posters are generated")
            continue
        try:
            exercise_id = normalize_slug(source.stem)
            if only and exercise_id not in only:
                continue
            exercise = db.get(Exercise, exercise_id)
            if exercise is None or exercise.source == "user" or exercise.owner_user_id:
                report.warnings.append(f"{source.name}: not a canonical exercise")
                continue
            if dry_run:
                probe(source)
                report.registered.append({"exercise_id": exercise_id, "action": "would prepare and stage privately"})
                continue
            with tempfile.TemporaryDirectory(prefix="primerep-media-") as temporary:
                prepared = prepare(source, Path(temporary))
                prefix = f"exercises/{exercise_id}"
                original_key = f"{prefix}/original.{prepared['source_hash']}{source.suffix.lower()}"
                video_key = f"{prefix}/demo.{prepared['video_hash']}.mp4"
                poster_key = f"{prefix}/poster.{prepared['poster_hash']}.jpg"
                upload_private(client, config, original_key, source, "application/octet-stream", report)
                upload_private(client, config, video_key, prepared["video"], "video/mp4", report)
                upload_private(client, config, poster_key, prepared["poster"], "image/jpeg", report)
                metadata = {**prepared["technical"], "original_key": original_key, "source_hash": prepared["source_hash"],
                            "poster_hash": prepared["poster_hash"], "variant": variant, "model_version": model_version,
                            "operator": operator, "staging_bucket": config.staging_bucket}
                with db.begin_nested():
                    asset = register_media(db, exercise_id=exercise_id, content_hash=prepared["video_hash"],
                                           storage_key=video_key, poster_key=poster_key, mime_type="video/mp4",
                                           metadata_json=metadata, production_method=production_method,
                                           rights_documentation=rights_documentation, technical_validated=True)
                report.registered.append({"exercise_id": exercise_id, "asset_id": asset.id, "approval_hash": asset.approval_hash})
        except Exception as exc:
            report.errors.append(f"{source.name}: {type(exc).__name__}: {str(exc) if isinstance(exc, ValueError) else 'staging failed; inspect local configuration'}")
    if not dry_run:
        db.commit()
    return report


def promote(db, config: StorageConfig, client, *, asset_id: str, expected_hash: str,
            operator: str, release_id: str, dry_run: bool = False, rollback: bool = False):
    from app.core.exercise_catalog_service import preflight_media_publication, publish_media
    config.validate(publish=True)
    if not operator.strip() or not release_id.strip():
        raise ValueError("Operator and release ID are required")
    asset = preflight_media_publication(db, asset_id, expected_hash, rollback=rollback)
    if asset.metadata_json.get("staging_bucket") != config.staging_bucket:
        raise ValueError("Asset belongs to a different staging bucket")
    if not asset.poster_key:
        raise ValueError("A validated poster is required")
    for key, content_type in [(asset.storage_key, "video/mp4"), (asset.poster_key, "image/jpeg")]:
        if not object_exists(client, config.staging_bucket, key):
            raise ValueError("Approved derivative is missing from staging")
        if not dry_run and not object_exists(client, config.bucket, key):
            client.copy_object(Bucket=config.bucket, Key=key,
                               CopySource={"Bucket": config.staging_bucket, "Key": key},
                               ContentType=content_type, CacheControl=IMMUTABLE_CACHE_CONTROL,
                               MetadataDirective="REPLACE")
    if dry_run:
        db.rollback()
        return asset_id
    published = publish_media(db, asset_id, expected_hash, public_base_url=config.cdn_base_url,
                              operator=operator, release_id=release_id, rollback=rollback)
    db.commit()
    return published.id


def load_config_from_env() -> StorageConfig:
    config = StorageConfig(bucket=os.getenv("ASSET_BUCKET", "").strip(),
                           cdn_base_url=os.getenv("ASSET_CDN_BASE_URL", "").strip(),
                           staging_bucket=os.getenv("ASSET_STAGING_BUCKET", "").strip(),
                           endpoint_url=os.getenv("ASSET_S3_ENDPOINT_URL", "").strip() or None,
                           region=os.getenv("ASSET_S3_REGION", "auto").strip() or "auto")
    config.validate()
    return config


def main():
    import json
    from app.core.database import SessionLocal
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage")
    stage.add_argument("--dir", type=Path, required=True)
    stage.add_argument("--production-method", choices=["filmed", "ai_generated", "hybrid"], required=True)
    stage.add_argument("--rights-file", type=Path, required=True)
    stage.add_argument("--variant", required=True)
    stage.add_argument("--model-version")
    stage.add_argument("--only")
    publish = sub.add_parser("publish")
    publish.add_argument("--asset-id", required=True)
    publish.add_argument("--hash", required=True)
    publish.add_argument("--release-id", required=True)
    publish.add_argument("--rollback", action="store_true", help="Restore a previously published, still approved asset")
    for command in (stage, publish):
        command.add_argument("--operator", required=True)
        command.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    config = load_config_from_env()
    client = None if args.command == "stage" and args.dry_run else build_s3_client(config)
    with SessionLocal() as db:
        if args.command == "stage":
            report = run(args.dir, db, config, client, operator=args.operator, production_method=args.production_method,
                         rights_documentation=args.rights_file.read_text(), variant=args.variant,
                         model_version=args.model_version, dry_run=args.dry_run,
                         only=set(args.only.split(",")) if args.only else None)
            print(json.dumps(report.__dict__, indent=2))
            if report.errors:
                raise SystemExit(1)
        else:
            result = promote(db, config, client, asset_id=args.asset_id, expected_hash=args.hash,
                             operator=args.operator, release_id=args.release_id, dry_run=args.dry_run, rollback=args.rollback)
            print(json.dumps({"asset_id": result, "dry_run": args.dry_run}))


if __name__ == "__main__":
    main()
