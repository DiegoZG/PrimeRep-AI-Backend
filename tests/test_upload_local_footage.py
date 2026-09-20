"""Storage is fake; database work is rolled back even when the uploader commits."""
import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.database import engine
from app.core.exercise_catalog_service import review_media
from app.models.exercise import Exercise
from app.models.exercise_catalog import ExerciseMediaAsset, ExerciseMediaPublication
from scripts import upload_local_footage as uploader
from scripts.media_pipeline import file_sha256


class NotFound(Exception):
    response = {"Error": {"Code": "404"}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.put_calls = []
        self.copy_calls = []
        self.fail_copy = False

    def head_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise NotFound()
        return {"ContentLength": len(self.objects[Bucket, Key])}

    def put_object(self, *, Bucket, Key, Body, ContentType, CacheControl):
        self.objects[Bucket, Key] = Body.read()
        self.put_calls.append((Bucket, Key, ContentType, CacheControl))

    def copy_object(self, *, Bucket, Key, CopySource, **kwargs):
        if self.fail_copy:
            raise RuntimeError("simulated storage failure")
        self.objects[Bucket, Key] = self.objects[CopySource["Bucket"], CopySource["Key"]]
        self.copy_calls.append((Bucket, Key, kwargs))


@pytest.fixture
def db():
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


@pytest.fixture
def exercise(db):
    row = Exercise(id="media_test_" + uuid.uuid4().hex, name="Media test", exercise_type="strength",
                   primary_muscle="quads", secondary_muscles=[], source="seed", is_active=True,
                   publication_status="legacy", demo_video_url="https://old.example/demo.mp4")
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def config():
    return uploader.StorageConfig(bucket="public", staging_bucket="private", cdn_base_url="https://media.example.com")


def prepare(source, directory):
    video = directory / "demo.mp4"
    poster = directory / "poster.jpg"
    video.write_bytes(b"derivative-" + source.read_bytes())
    poster.write_bytes(b"poster-" + source.read_bytes())
    return {"video": video, "poster": poster, "video_hash": file_sha256(video), "poster_hash": file_sha256(poster),
            "source_hash": file_sha256(source), "technical": {"profile": "test", "derivative": {"width": 720, "height": 1280}}}


def stage(tmp_path, db, exercise, config, client, **options):
    path = tmp_path / f"{exercise.id}.mp4"
    if not path.exists():
        path.write_bytes(b"original")
    kwargs = {"operator": "test-operator", "production_method": "filmed", "rights_documentation": "Test fixture rights only", "variant": "three-quarter", "prepare": prepare}
    kwargs.update(options)
    return uploader.run(tmp_path, db, config, client, **kwargs)


def approvals(db, asset):
    review_media(db, asset.id, asset.approval_hash, reviewer="test-trainer", role="trainer")
    review_media(db, asset.id, asset.approval_hash, reviewer="test-publisher", role="publisher")


def publish(db, config, client, asset, **options):
    return uploader.promote(db, config, client, asset_id=asset.id, expected_hash=asset.approval_hash,
                            operator="test-operator", release_id="test-release", **options)


def test_stage_is_private_idempotent_and_cannot_repoint_exercise(tmp_path, db, exercise, config):
    client = FakeS3()
    first = stage(tmp_path, db, exercise, config, client)
    assert not first.errors
    assert len(client.put_calls) == 3
    assert {call[0] for call in client.put_calls} == {"private"}
    assert all(call[3] == "private, no-store" for call in client.put_calls)
    assert exercise.demo_video_url == "https://old.example/demo.mp4"
    again = stage(tmp_path, db, exercise, config, client)
    assert again.registered == first.registered
    assert len(client.put_calls) == 3
    assert db.query(ExerciseMediaAsset).filter_by(exercise_id=exercise.id).count() == 1


def test_publication_copies_only_approved_derivative_and_poster(tmp_path, db, exercise, config):
    client = FakeS3()
    report = stage(tmp_path, db, exercise, config, client)
    asset = db.get(ExerciseMediaAsset, report.registered[0]["asset_id"])
    with pytest.raises(ValueError, match="approvals"):
        publish(db, config, client, asset)
    assert client.copy_calls == []
    approvals(db, asset)
    publish(db, config, client, asset)
    assert len(client.copy_calls) == 2
    assert all("original." not in key for bucket, key, _ in client.copy_calls)
    assert all(call[2]["CacheControl"] == uploader.IMMUTABLE_CACHE_CONTROL for call in client.copy_calls)
    assert exercise.demo_video_url.startswith("https://media.example.com/exercises/")
    publish(db, config, client, asset)
    assert len(client.copy_calls) == 2


def test_copy_failure_preserves_existing_public_pointer(tmp_path, db, exercise, config):
    client = FakeS3()
    report = stage(tmp_path, db, exercise, config, client)
    asset = db.get(ExerciseMediaAsset, report.registered[0]["asset_id"])
    approvals(db, asset)
    client.fail_copy = True
    with pytest.raises(RuntimeError):
        publish(db, config, client, asset)
    assert exercise.demo_video_url == "https://old.example/demo.mp4"
    assert db.query(ExerciseMediaPublication).filter_by(asset_id=asset.id).count() == 0


def test_changed_rights_require_new_approval_identity(tmp_path, db, exercise, config):
    client = FakeS3()
    first = stage(tmp_path, db, exercise, config, client)
    second = stage(tmp_path, db, exercise, config, client, rights_documentation="Updated fixture rights")
    assert first.registered[0]["asset_id"] != second.registered[0]["asset_id"]
    assert len(client.put_calls) == 3
    changed = db.get(ExerciseMediaAsset, second.registered[0]["asset_id"])
    with pytest.raises(ValueError):
        publish(db, config, client, changed)


def test_refilming_uses_new_immutable_keys(tmp_path, db, exercise, config):
    client = FakeS3()
    first = stage(tmp_path, db, exercise, config, client)
    (tmp_path / f"{exercise.id}.mp4").write_bytes(b"refilmed")
    second = stage(tmp_path, db, exercise, config, client)
    assert len(client.put_calls) == 6
    assert first.registered[0]["approval_hash"] != second.registered[0]["approval_hash"]


def test_unknown_and_invalid_sources_do_not_abort_valid_stage(tmp_path, db, exercise, config):
    (tmp_path / "unknown.mp4").write_bytes(b"unknown")
    (tmp_path / "poster.jpg").write_bytes(b"poster")
    client = FakeS3()
    report = stage(tmp_path, db, exercise, config, client)
    assert len(report.registered) == 1
    assert len(report.warnings) == 2


def test_staging_dry_run_does_not_upload_or_write(tmp_path, db, exercise, config):
    client = FakeS3()
    report = stage(tmp_path, db, exercise, config, client, dry_run=True, probe=lambda path: {})
    assert not report.errors
    assert not client.put_calls
    assert db.query(ExerciseMediaAsset).filter_by(exercise_id=exercise.id).count() == 0


def test_rejects_public_staging_and_insecure_delivery(config):
    config.staging_bucket = config.bucket
    with pytest.raises(ValueError, match="separate private"):
        config.validate()
    config.staging_bucket = "private"
    config.cdn_base_url = "http://media.example.com"
    with pytest.raises(ValueError, match="HTTPS"):
        config.validate(publish=True)


def test_only_filter_and_ai_provenance_validation(tmp_path, db, exercise, config):
    client = FakeS3()
    report = stage(tmp_path, db, exercise, config, client, only={"another_exercise"})
    assert not report.registered and not client.put_calls
    with pytest.raises(ValueError, match="model/tool"):
        stage(tmp_path, db, exercise, config, client, production_method="ai_generated")


def test_head_errors_are_not_treated_as_missing():
    class Forbidden:
        def head_object(self, **kwargs):
            raise RuntimeError("permission denied")
    with pytest.raises(RuntimeError):
        uploader.object_exists(Forbidden(), "private", "key")


def test_normalize_slug_and_config_requires_private_bucket(monkeypatch):
    assert uploader.normalize_slug("Bench Press") == "bench_press"
    assert uploader.normalize_slug("bench-press") == "bench_press"
    with pytest.raises(ValueError):
        uploader.normalize_slug("../secret")
    monkeypatch.delenv("ASSET_STAGING_BUCKET", raising=False)
    with pytest.raises(ValueError, match="ASSET_STAGING_BUCKET"):
        uploader.load_config_from_env()
