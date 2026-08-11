"""
Tests for scripts/upload_local_footage.py.

The storage client is faked in every test — these never touch S3/R2 and never
require credentials. The database, however, is the real dev database (same
convention as the rest of the suite), so each test restores the exercise rows
it touches.

Note: These tests assume migrations have been run and the exercise seed data
exists in the dev DB.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

from app.core.database import SessionLocal
from app.models.exercise import Exercise

# Loaded by path because scripts/ is deliberately not an importable package.
# It has to be registered in sys.modules before it executes, or the dataclasses
# inside it cannot resolve their own module when evaluating annotations.
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "upload_local_footage.py"
_spec = importlib.util.spec_from_file_location("upload_local_footage", _SCRIPT_PATH)
uploader = importlib.util.module_from_spec(_spec)
sys.modules["upload_local_footage"] = uploader
_spec.loader.exec_module(uploader)

# Real seeded ids. DEADLIFT is never referenced by a test fixture file — it is
# the control for "every other exercise is unaffected".
SQUAT = "squat"
BENCH = "bench_press"
DEADLIFT = "deadlift"
TOUCHED_IDS = [SQUAT, BENCH, DEADLIFT]

CDN_BASE_URL = "https://cdn.example.com"


class NotFound(Exception):
    """Shaped like the botocore ClientError raised by head_object on a miss."""

    response = {
        "Error": {"Code": "404", "Message": "Not Found"},
        "ResponseMetadata": {"HTTPStatusCode": 404},
    }


class FakeS3Client:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[str] = []
        self.head_calls: list[str] = []
        self.fail_on_keys: set[str] = set()

    def head_object(self, Bucket: str, Key: str):
        self.head_calls.append(Key)
        if Key not in self.objects:
            raise NotFound()
        return {"ContentLength": len(self.objects[Key])}

    def put_object(self, Bucket: str, Key: str, Body, ContentType: str, CacheControl: str):
        if Key in self.fail_on_keys:
            raise RuntimeError("simulated upload failure")
        self.objects[Key] = Body.read()
        self.put_calls.append(Key)
        return {}


@pytest.fixture
def client() -> FakeS3Client:
    return FakeS3Client()


@pytest.fixture
def config():
    return uploader.StorageConfig(bucket="test-bucket", cdn_base_url=CDN_BASE_URL)


@pytest.fixture
def db():
    """A real session that restores every exercise row the tests can touch."""
    session = SessionLocal()
    rows = session.query(Exercise).filter(Exercise.id.in_(TOUCHED_IDS)).all()
    original = {row.id: (row.demo_video_url, row.image_url) for row in rows}
    assert len(original) == len(TOUCHED_IDS), "expected seed exercises to be present"

    try:
        yield session
    finally:
        session.rollback()
        for exercise_id, (demo_video_url, image_url) in original.items():
            row = session.query(Exercise).filter(Exercise.id == exercise_id).first()
            row.demo_video_url = demo_video_url
            row.image_url = image_url
        session.commit()
        session.close()


def write_file(directory: Path, name: str, content: bytes = b"fake-media-bytes") -> Path:
    path = directory / name
    path.write_bytes(content)
    return path


def reload(db, exercise_id: str) -> Exercise:
    db.expire_all()
    return db.query(Exercise).filter(Exercise.id == exercise_id).first()


def test_normalize_slug_accepts_hyphens_and_spaces():
    assert uploader.normalize_slug("bench-press") == "bench_press"
    assert uploader.normalize_slug("Bench Press") == "bench_press"
    assert uploader.normalize_slug("squat") == "squat"


def test_discover_assets_routes_videos_and_posters_to_the_right_columns(tmp_path):
    write_file(tmp_path, "bench-press.mp4")
    write_file(tmp_path, "squat.jpg")
    write_file(tmp_path, "notes.txt")

    assets, warnings = uploader.discover_assets(tmp_path)

    by_id = {asset.exercise_id: asset for asset in assets}
    assert by_id["bench_press"].column == uploader.VIDEO_COLUMN
    assert by_id["squat"].column == uploader.POSTER_COLUMN
    assert any("notes.txt" in warning for warning in warnings)


def test_discover_assets_warns_when_two_files_claim_the_same_column(tmp_path):
    write_file(tmp_path, "squat.mp4")
    write_file(tmp_path, "squat.mov")

    assets, warnings = uploader.discover_assets(tmp_path)

    assert len(assets) == 1
    assert any("already claims" in warning for warning in warnings)


def test_upload_writes_cdn_urls_to_the_exercise_row(tmp_path, db, client, config):
    write_file(tmp_path, "squat.mp4", b"squat-clip")
    write_file(tmp_path, "squat.jpg", b"squat-poster")

    report = uploader.run(tmp_path, db, config, client)

    assert not report.errors
    assert len(client.put_calls) == 2

    row = reload(db, SQUAT)
    assert row.demo_video_url.startswith(f"{CDN_BASE_URL}/exercises/squat/demo.")
    assert row.demo_video_url.endswith(".mp4")
    assert row.image_url.startswith(f"{CDN_BASE_URL}/exercises/squat/poster.")

    # Each stored URL is the CDN origin joined to a key that was really uploaded.
    for url in (row.demo_video_url, row.image_url):
        assert url.rsplit(f"{CDN_BASE_URL}/", 1)[1] in client.put_calls


def test_upload_sets_immutable_caching_and_a_real_content_type(tmp_path, db, client, config):
    write_file(tmp_path, "squat.mp4")
    recorded = {}

    original_put = client.put_object

    def capture(**kwargs):
        recorded.update(kwargs)
        return original_put(**kwargs)

    client.put_object = capture
    uploader.run(tmp_path, db, config, client)

    assert recorded["ContentType"] == "video/mp4"
    assert recorded["CacheControl"] == uploader.IMMUTABLE_CACHE_CONTROL


def test_rerunning_with_unchanged_files_uploads_nothing_and_rewrites_nothing(
    tmp_path, db, client, config
):
    write_file(tmp_path, "squat.mp4", b"squat-clip")
    uploader.run(tmp_path, db, config, client)
    url_after_first_run = reload(db, SQUAT).demo_video_url

    second = uploader.run(tmp_path, db, config, client)

    assert len(client.put_calls) == 1
    assert second.uploaded == []
    assert second.rows_updated == []
    assert second.already_in_storage
    assert second.rows_unchanged == [f"{SQUAT}.{uploader.VIDEO_COLUMN}"]
    assert reload(db, SQUAT).demo_video_url == url_after_first_run


def test_refilmed_clip_gets_a_new_key_and_repoints_the_row(tmp_path, db, client, config):
    write_file(tmp_path, "squat.mp4", b"first-take")
    uploader.run(tmp_path, db, config, client)
    first_url = reload(db, SQUAT).demo_video_url

    write_file(tmp_path, "squat.mp4", b"second-take-different-bytes")
    report = uploader.run(tmp_path, db, config, client)

    assert len(client.put_calls) == 2
    assert report.rows_updated == [f"{SQUAT}.{uploader.VIDEO_COLUMN}"]

    second_url = reload(db, SQUAT).demo_video_url
    assert second_url != first_url


def test_one_clip_leaves_every_other_exercise_untouched(tmp_path, db, client, config):
    write_file(tmp_path, "squat.mp4")

    uploader.run(tmp_path, db, config, client)

    assert reload(db, SQUAT).demo_video_url is not None
    # No poster was supplied, so the placeholder guard for squat is untouched.
    assert reload(db, SQUAT).image_url is None
    assert reload(db, DEADLIFT).demo_video_url is None
    assert reload(db, DEADLIFT).image_url is None


def test_unknown_slug_empty_file_and_failed_upload_do_not_abort_the_batch(
    tmp_path, db, client, config
):
    write_file(tmp_path, "not_a_real_exercise.mp4")
    write_file(tmp_path, "bench_press.mp4", b"")
    write_file(tmp_path, "deadlift.mp4", b"deadlift-clip")
    write_file(tmp_path, "squat.mp4", b"squat-clip")

    deadlift_key = uploader.build_key(
        uploader.Asset(DEADLIFT, tmp_path / "deadlift.mp4", uploader.VIDEO_COLUMN),
        uploader.file_sha256(tmp_path / "deadlift.mp4"),
    )
    client.fail_on_keys.add(deadlift_key)

    report = uploader.run(tmp_path, db, config, client)

    assert any("no exercise with id" in warning for warning in report.warnings)
    assert any("file is empty" in warning for warning in report.warnings)
    assert any("deadlift.mp4" in error for error in report.errors)

    # The one healthy file still made it all the way through.
    assert reload(db, SQUAT).demo_video_url is not None
    assert reload(db, BENCH).demo_video_url is None
    assert reload(db, DEADLIFT).demo_video_url is None


def test_head_object_errors_other_than_not_found_are_not_swallowed(tmp_path, db, config):
    class BrokenClient(FakeS3Client):
        def head_object(self, Bucket, Key):
            raise PermissionError("access denied")

    write_file(tmp_path, "squat.mp4")

    report = uploader.run(tmp_path, db, config, BrokenClient())

    assert any("access denied" in error for error in report.errors)
    assert reload(db, SQUAT).demo_video_url is None


def test_only_filter_limits_the_run(tmp_path, db, client, config):
    write_file(tmp_path, "squat.mp4")
    write_file(tmp_path, "bench-press.mp4")

    uploader.run(tmp_path, db, config, client, only={SQUAT})

    assert len(client.put_calls) == 1
    assert reload(db, SQUAT).demo_video_url is not None
    assert reload(db, BENCH).demo_video_url is None


def test_dry_run_reports_changes_without_uploading_or_writing(tmp_path, db, client, config):
    write_file(tmp_path, "squat.mp4")

    report = uploader.run(tmp_path, db, config, client, dry_run=True)

    assert report.rows_updated == [f"{SQUAT}.{uploader.VIDEO_COLUMN}"]
    assert report.uploaded
    assert client.put_calls == []
    assert client.head_calls == []
    assert reload(db, SQUAT).demo_video_url is None


def test_load_config_from_env_requires_bucket_and_cdn(monkeypatch):
    monkeypatch.setenv("ASSET_BUCKET", "")
    monkeypatch.setenv("ASSET_CDN_BASE_URL", "")
    with pytest.raises(SystemExit):
        uploader.load_config_from_env()

    monkeypatch.setenv("ASSET_BUCKET", "primerep-assets")
    monkeypatch.setenv("ASSET_CDN_BASE_URL", "https://cdn.primerep.app/")
    monkeypatch.setenv("ASSET_S3_ENDPOINT_URL", "https://acct.r2.cloudflarestorage.com")

    config = uploader.load_config_from_env()

    assert config.bucket == "primerep-assets"
    assert config.endpoint_url == "https://acct.r2.cloudflarestorage.com"
    # Trailing slash on the origin must not produce a doubled separator.
    assert config.url_for("exercises/squat/demo.abc.mp4") == (
        "https://cdn.primerep.app/exercises/squat/demo.abc.mp4"
    )
