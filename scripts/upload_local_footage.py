"""
Upload filmed exercise clips (and optional poster images) to object storage and
point the `exercises` table at them.

This is OFFLINE TOOLING — it is never run by the app or by Alembic. Run it by
hand whenever new footage has been filmed. It is safe to re-run: only files
whose contents have actually changed cause an upload or a database write.

Naming
------
Files are matched to exercises by filename. The stem is the exercise id, with
hyphens and spaces accepted as substitutes for underscores:

    footage/bench_press.mp4   -> exercises.demo_video_url for "bench_press"
    footage/bench-press.jpg   -> exercises.image_url      for "bench_press"

A file whose stem matches no exercise is reported and skipped — it never
aborts the rest of the batch.

Storage
-------
Any S3-compatible service works. Leave ASSET_S3_ENDPOINT_URL unset for AWS S3,
or point it at https://<account-id>.r2.cloudflarestorage.com for Cloudflare R2.
Credentials come from the usual AWS environment variables.

Object keys embed a content hash:

    exercises/bench_press/demo.3f9a1c02b7d4.mp4

so the bytes behind a key never change. That makes re-uploads a no-op (the key
is already there), lets the CDN cache objects permanently, and guarantees a
re-filmed clip is served immediately instead of sitting behind a stale cache.
Superseded objects stay in the bucket; clear them out with a lifecycle rule if
they ever add up.

Usage
-----
    # Show what would happen, without contacting storage or writing to the DB
    python scripts/upload_local_footage.py --dir footage --dry-run

    # Upload and update the database
    python scripts/upload_local_footage.py --dir footage

    # Limit the run to specific exercises
    python scripts/upload_local_footage.py --dir footage --only squat,bench_press
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
POSTER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".webm": "video/webm",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

VIDEO_COLUMN = "demo_video_url"
POSTER_COLUMN = "image_url"

HASH_PREFIX_LENGTH = 12

# Safe because keys are content-addressed — see the module docstring.
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


@dataclass
class StorageConfig:
    bucket: str
    cdn_base_url: str
    endpoint_url: str | None = None
    region: str = "auto"

    def url_for(self, key: str) -> str:
        return f"{self.cdn_base_url.rstrip('/')}/{key}"


@dataclass
class Asset:
    """One local file destined for one column of one exercise row."""

    exercise_id: str
    path: Path
    column: str

    @property
    def is_video(self) -> bool:
        return self.column == VIDEO_COLUMN


@dataclass
class RunReport:
    uploaded: list[str] = field(default_factory=list)
    already_in_storage: list[str] = field(default_factory=list)
    rows_updated: list[str] = field(default_factory=list)
    rows_unchanged: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def normalize_slug(stem: str) -> str:
    """Map a filename stem onto an exercise id (`bench-press` -> `bench_press`)."""
    return stem.strip().lower().replace("-", "_").replace(" ", "_")


def content_type_for(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_key(asset: Asset, digest: str) -> str:
    name = "demo" if asset.is_video else "poster"
    suffix = asset.path.suffix.lower()
    return f"exercises/{asset.exercise_id}/{name}.{digest[:HASH_PREFIX_LENGTH]}{suffix}"


def discover_assets(directory: Path) -> tuple[list[Asset], list[str]]:
    """Collect uploadable files from `directory`, plus warnings about the rest."""
    if not directory.is_dir():
        raise SystemExit(f"Footage directory not found: {directory}")

    assets: list[Asset] = []
    warnings: list[str] = []
    claimed: dict[tuple[str, str], Path] = {}

    for path in sorted(directory.iterdir()):
        if path.is_dir() or path.name.startswith("."):
            continue

        suffix = path.suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            column = VIDEO_COLUMN
        elif suffix in POSTER_EXTENSIONS:
            column = POSTER_COLUMN
        else:
            warnings.append(f"{path.name}: unsupported file type, skipped")
            continue

        exercise_id = normalize_slug(path.stem)
        existing = claimed.get((exercise_id, column))
        if existing:
            warnings.append(
                f"{path.name}: '{existing.name}' already claims {column} for "
                f"'{exercise_id}', skipped"
            )
            continue

        claimed[(exercise_id, column)] = path
        assets.append(Asset(exercise_id=exercise_id, path=path, column=column))

    return assets, warnings


def _is_not_found(exc: Exception) -> bool:
    """True for an S3 "object does not exist" error, false for anything else.

    Matched structurally rather than by catching botocore's ClientError so that
    tests can stand in a fake client without importing botocore.
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False

    code = str(response.get("Error", {}).get("Code", ""))
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"404", "NoSuchKey", "NotFound"} or status == 404


def object_exists(client, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception as exc:
        if _is_not_found(exc):
            return False
        raise


def build_s3_client(config: StorageConfig):
    """boto3 is imported lazily so the module stays importable without it."""
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url or None,
        region_name=config.region or None,
    )


def run(
    directory: Path,
    db,
    config: StorageConfig,
    client=None,
    *,
    dry_run: bool = False,
    only: set[str] | None = None,
) -> RunReport:
    """Upload every asset in `directory` and repoint the matching exercise rows.

    Failures are recorded per file rather than raised, so one unreadable clip
    cannot cost you the rest of a batch.
    """
    from app.models.exercise import Exercise

    assets, warnings = discover_assets(directory)
    report = RunReport(warnings=warnings)

    if only:
        assets = [asset for asset in assets if asset.exercise_id in only]

    for asset in assets:
        label = f"{asset.exercise_id}.{asset.column}"
        try:
            exercise = (
                db.query(Exercise).filter(Exercise.id == asset.exercise_id).first()
            )
            if exercise is None:
                report.warnings.append(
                    f"{asset.path.name}: no exercise with id '{asset.exercise_id}', skipped"
                )
                continue

            if asset.path.stat().st_size == 0:
                report.warnings.append(f"{asset.path.name}: file is empty, skipped")
                continue

            key = build_key(asset, file_sha256(asset.path))
            url = config.url_for(key)

            if dry_run:
                report.uploaded.append(key)
            elif object_exists(client, config.bucket, key):
                report.already_in_storage.append(key)
            else:
                with asset.path.open("rb") as fh:
                    client.put_object(
                        Bucket=config.bucket,
                        Key=key,
                        Body=fh,
                        ContentType=content_type_for(asset.path),
                        CacheControl=IMMUTABLE_CACHE_CONTROL,
                    )
                report.uploaded.append(key)

            if getattr(exercise, asset.column) == url:
                report.rows_unchanged.append(label)
                continue

            if not dry_run:
                setattr(exercise, asset.column, url)
            report.rows_updated.append(label)
        except Exception as exc:
            report.errors.append(f"{asset.path.name}: {exc}")

    if dry_run:
        db.rollback()
    else:
        db.commit()

    return report


def load_config_from_env() -> StorageConfig:
    bucket = os.getenv("ASSET_BUCKET", "").strip()
    cdn_base_url = os.getenv("ASSET_CDN_BASE_URL", "").strip()

    missing = [
        name
        for name, value in (("ASSET_BUCKET", bucket), ("ASSET_CDN_BASE_URL", cdn_base_url))
        if not value
    ]
    if missing:
        raise SystemExit(
            f"{' and '.join(missing)} must be set. See .env.example for the full "
            "object-storage configuration."
        )

    return StorageConfig(
        bucket=bucket,
        cdn_base_url=cdn_base_url,
        endpoint_url=os.getenv("ASSET_S3_ENDPOINT_URL", "").strip() or None,
        region=os.getenv("ASSET_S3_REGION", "auto").strip() or "auto",
    )


def print_report(report: RunReport, *, dry_run: bool) -> None:
    verb = "Would upload" if dry_run else "Uploaded"
    print(f"\n{verb} {len(report.uploaded)} object(s).")
    for key in report.uploaded:
        print(f"  + {key}")

    if report.already_in_storage:
        print(f"\n{len(report.already_in_storage)} object(s) already in storage, unchanged.")

    verb = "Would update" if dry_run else "Updated"
    print(f"\n{verb} {len(report.rows_updated)} exercise field(s).")
    for label in report.rows_updated:
        print(f"  * {label}")

    if report.rows_unchanged:
        print(f"\n{len(report.rows_unchanged)} exercise field(s) already correct.")

    if report.warnings:
        print(f"\n{len(report.warnings)} warning(s):")
        for warning in report.warnings:
            print(f"  ! {warning}")

    if report.errors:
        print(f"\n{len(report.errors)} error(s):")
        for error in report.errors:
            print(f"  x {error}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dir",
        default="footage",
        help="Folder of clips named by exercise id (default: footage)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without contacting storage or writing to the DB",
    )
    parser.add_argument(
        "--only",
        help="Comma-separated exercise ids to limit the run to",
    )
    args = parser.parse_args()

    config = load_config_from_env()
    only = (
        {normalize_slug(part) for part in args.only.split(",") if part.strip()}
        if args.only
        else None
    )

    from app.core.database import SessionLocal

    client = None if args.dry_run else build_s3_client(config)
    db = SessionLocal()
    try:
        report = run(
            Path(args.dir),
            db,
            config,
            client,
            dry_run=args.dry_run,
            only=only,
        )
    finally:
        db.close()

    print_report(report, dry_run=args.dry_run)
    if report.errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
