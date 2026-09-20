import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.database import SessionLocal, get_db
from app.core.exercise_catalog_service import (
    import_entry, publish_revision, review_revision, submit_revision,
    register_media, review_media, publish_media,
)
from app.core.exercise_service import count_exercises, exercise_to_dict, get_exercise, list_exercises
from app.core.workout_service import get_eligible_exercises
from app.main import app
from app.models.exercise import Exercise
from app.models.exercise_catalog import ExercisePublication, ExerciseRevision
from app.schemas.exercise_catalog import CatalogEntry
from app.core.security.deps import get_current_user, get_current_user_optional
from app.models.user import User


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def entry(**changes):
    payload = dict(
        id=f"catalog_test_{uuid.uuid4().hex}", name="Catalog Test Press", exercise_type="strength",
        primary_muscle="chest", secondary_muscles=["triceps"], equipment_ids=[], aliases=["Test Push"],
        movement_pattern="horizontal_push", resistance_modality="bodyweight", difficulty="beginner",
        laterality="bilateral", load_profile={"basis": "bodyweight", "implement_count": 1},
        structured_content={"steps": ["Set up with control.", "Move through your comfortable range."],
                            "cues": ["Keep control."], "mistakes": ["Rushing the movement."],
                            "benefits": "Practices pressing strength.", "beginner_guidance": "Use a manageable range."},
        generation_eligible=True, tracking_mode="reps",
        sources=[{"title": "Reference", "url": "https://example.com/reference", "accessed_at": "2026-09-20", "claims": ["Test-only source fixture."]}],
    )
    payload.update(changes)
    return CatalogEntry.model_validate(payload)


def approve(db, revision):
    submit_revision(db, revision.id, revision.content_hash)
    review_revision(db, revision.id, revision.content_hash, reviewer="trainer@example.test", role="trainer", decision="approved")
    review_revision(db, revision.id, revision.content_hash, reviewer="publisher@example.test", role="publisher", decision="approved")


def publish(db, revision, **kwargs):
    return publish_revision(db, revision.id, revision.content_hash, operator="operator@example.test", release_id="test-release", **kwargs)


def test_draft_is_invisible_and_import_idempotent(db):
    payload = entry()
    revision = import_entry(db, payload, "author@example.test")
    assert import_entry(db, payload, "author@example.test").id == revision.id
    assert get_exercise(db, payload.id) is None
    assert all(item.id != payload.id for item in list_exercises(db, q="Catalog Test"))
    assert all(item.id != payload.id for item in get_eligible_exercises(db, owned_equipment_ids=set()))
    assert db.query(ExerciseRevision).filter_by(exercise_id=payload.id).count() == 1


def test_publication_requires_reviewed_exact_revision_and_independent_people(db):
    revision = import_entry(db, entry(), "author")
    with pytest.raises(ValueError, match="not been submitted"):
        publish(db, revision)
    submit_revision(db, revision.id, revision.content_hash)
    with pytest.raises(ValueError, match="Trainer approval"):
        review_revision(db, revision.id, revision.content_hash, reviewer="publisher", role="publisher", decision="approved")
    review_revision(db, revision.id, revision.content_hash, reviewer="trainer", role="trainer", decision="approved")
    with pytest.raises(ValueError, match="different people"):
        review_revision(db, revision.id, revision.content_hash, reviewer="trainer", role="publisher", decision="approved")
    with pytest.raises(ValueError, match="hash mismatch"):
        submit_revision(db, revision.id, "0" * 64)
    review_revision(db, revision.id, revision.content_hash, reviewer="publisher", role="publisher", decision="approved")
    exercise = publish(db, revision)
    assert get_exercise(db, exercise.id) is exercise
    assert exercise.how_to.startswith("1. Set up")
    assert exercise.content_version == revision.content_hash
    assert exercise_to_dict(exercise, user_id=None)["structured_content"]["steps"]
    assert list_exercises(db, q="Test Push")[0].id == exercise.id


def test_latest_rejection_blocks_publish_within_same_transaction(db):
    revision = import_entry(db, entry(), "author")
    approve(db, revision)
    review_revision(db, revision.id, revision.content_hash, reviewer="trainer@example.test", role="trainer", decision="rejected", comments="Needs correction")
    with pytest.raises(ValueError, match="approval required"):
        publish(db, revision)
    assert db.get(Exercise, revision.exercise_id).publication_status == "draft"


def test_new_revision_does_not_inherit_approval_and_rollback_preserves_identity(db):
    original = entry()
    first = import_entry(db, original, "author")
    approve(db, first)
    exercise = publish(db, first)
    second = import_entry(db, original.model_copy(update={"name": "Updated Test Press"}), "author")
    submit_revision(db, second.id, second.content_hash)
    with pytest.raises(ValueError, match="approval required"):
        publish(db, second)
    assert exercise.name == original.name
    approve(db, second)
    publish(db, second)
    assert exercise.name == "Updated Test Press"
    publish(db, first, rollback=True)
    assert exercise.id == original.id and exercise.name == original.name
    assert db.query(ExercisePublication).filter_by(exercise_id=exercise.id).count() == 3


def test_database_rejects_submitted_payload_rewrite(db):
    revision = import_entry(db, entry(), "author")
    submit_revision(db, revision.id, revision.content_hash)
    with pytest.raises(DBAPIError, match="immutable"):
        with db.begin_nested():
            db.execute(text("UPDATE exercise_revisions SET payload = '{}'::jsonb WHERE id = :id"), {"id": revision.id})


def test_unknown_source_never_becomes_shared(db):
    exercise = Exercise(id=f"unknown_{uuid.uuid4().hex}", name="Unknown Source", exercise_type="strength", primary_muscle="chest", secondary_muscles=[], source="unreviewed_import")
    db.add(exercise)
    db.flush()
    assert get_exercise(db, exercise.id) is None


def test_pagination_beyond_200_and_api_metadata(db):
    prefix = "Pagination " + uuid.uuid4().hex[:8]
    for number in range(215):
        db.add(Exercise(id=f"page_{uuid.uuid4().hex}", name=f"{prefix} {number:03}", exercise_type="strength", primary_muscle="chest", secondary_muscles=[], source="seed"))
    db.flush()
    assert count_exercises(db, q=prefix) == 215
    pages = [list_exercises(db, q=prefix, limit=50, offset=n) for n in range(0, 250, 50)]
    assert [len(page) for page in pages] == [50, 50, 50, 50, 15]
    assert len({item.id for page in pages for item in page}) == 215
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get("/v1/exercises", params={"q": prefix, "offset": 200, "limit": 50})
        assert response.status_code == 200
        assert response.json()["total"] == 215
        assert response.json()["has_more"] is False
        assert len(response.json()["items"]) == 15
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_tracking_mode_blocks_timed_strength_generation():
    with pytest.raises(ValueError, match="generation eligible"):
        entry(tracking_mode="duration")
    entry(tracking_mode="duration", generation_eligible=False)


def test_media_stays_private_until_both_reviews_and_immutable_rights(db):
    values = dict(exercise_id="push_up", content_hash="a" * 64, storage_key="videos/abc/demo.mp4", poster_key="videos/abc/poster.jpg", mime_type="video/mp4", metadata_json={"width": 720, "height": 1280}, production_method="filmed", rights_documentation="Signed participant release, private record test", technical_validated=True)
    asset = register_media(db, **values)
    assert asset.published_url is None
    assert register_media(db, **values).id == asset.id
    with pytest.raises(ValueError, match="approvals required"):
        publish_media(db, asset.id, asset.approval_hash, public_base_url="https://media.example.test", operator="operator", release_id="media1")
    review_media(db, asset.id, asset.approval_hash, reviewer="trainer", role="trainer")
    review_media(db, asset.id, asset.approval_hash, reviewer="publisher", role="publisher")
    publish_media(db, asset.id, asset.approval_hash, public_base_url="https://media.example.test", operator="operator", release_id="media1")
    exercise = db.get(Exercise, "push_up")
    assert exercise.published_media["url"].startswith("https://media.example.test/")
    assert "rights_documentation" not in exercise.published_media
    changed = register_media(db, **dict(values, rights_documentation="Replacement release"))
    assert changed.id != asset.id and changed.approved_at is None
    with pytest.raises(DBAPIError, match="immutable"):
        with db.begin_nested():
            db.execute(text("UPDATE exercise_media_assets SET rights_documentation='changed' WHERE id=:id"), {"id": asset.id})


def test_failed_publication_rolls_back_read_model_and_aliases(db):
    revision = import_entry(db, entry(), "author")
    approve(db, revision)
    with pytest.raises(RuntimeError):
        with db.begin_nested():
            publish(db, revision)
            raise RuntimeError("Interrupted before transaction commit")
    db.expire_all()
    assert get_exercise(db, revision.exercise_id) is None
    assert db.query(ExercisePublication).filter_by(revision_id=revision.id).count() == 0


def test_draft_and_private_visibility_across_detail_search_qa_and_template(db):
    owner = User(email=f"owner-{uuid.uuid4().hex}@example.test", preferred_name="Owner", password_hash="test")
    other = User(email=f"other-{uuid.uuid4().hex}@example.test", preferred_name="Other", password_hash="test")
    db.add_all([owner, other])
    db.flush()
    private = Exercise(id=f"private_{uuid.uuid4().hex}", name="Private Catalog Press", exercise_type="strength", primary_muscle="chest", secondary_muscles=[], source="user", owner_user_id=owner.id)
    db.add(private)
    revision = import_entry(db, entry(name="Private Catalog Draft"), "author")
    draft = db.get(Exercise, revision.exercise_id)
    draft.is_active = True
    db.flush()
    assert get_exercise(db, private.id, user_id=owner.id) is private
    assert get_exercise(db, private.id, user_id=other.id) is None
    app.dependency_overrides.update({get_db: lambda: db, get_current_user: lambda: other, get_current_user_optional: lambda: other})
    try:
        client = TestClient(app)
        for hidden in (private, draft):
            assert client.get(f"/v1/exercises/{hidden.id}").status_code == 404
            assert client.get(f"/v1/exercises/{hidden.id}/questions").status_code == 404
            assert client.post(f"/v1/exercises/{hidden.id}/ask", json={"question": "How?"}).status_code == 404
            assert client.post(f"/v1/exercises/{hidden.id}/favorite").status_code == 404
            assert client.get(f"/v1/exercises/{hidden.id}/substitutions").status_code == 404
        result = client.get("/v1/explore/search", params={"query": "Private Catalog"}).json()
        assert not {private.id, draft.id} & {row["id"] for row in result["exercises"]}
        assert all(row.id not in {private.id, draft.id} for row in get_eligible_exercises(db, user_id=other.id, owned_equipment_ids=set()))
    finally:
        for dependency in (get_db, get_current_user, get_current_user_optional):
            app.dependency_overrides.pop(dependency, None)


def test_reviewed_substitutions_use_same_compatibility_at_activation(db):
    from app.core.workout_template_service import compatible_substitution, list_substitutions
    first = import_entry(db, entry(), "author")
    second = import_entry(db, entry(exercise_type="bodyweight", name="Compatible push"), "author")
    third = import_entry(db, entry(name="Incompatible fly", movement_pattern="adduction"), "author")
    for revision in (first, second, third):
        approve(db, revision)
        publish(db, revision)
    original, compatible, incompatible = [db.get(Exercise, revision.exercise_id) for revision in (first, second, third)]
    assert compatible_substitution(original, compatible)
    assert compatible_substitution(exercise_to_dict(original, user_id=None), compatible)
    assert not compatible_substitution(original, incompatible)
    candidates = list_substitutions(db, "test-unowned-user", original.id, owned_only=True)
    assert compatible.id in {row.id for row in candidates}
    assert incompatible.id not in {row.id for row in candidates}


def test_media_rollback_restores_prior_approved_asset(db):
    common = dict(exercise_id="push_up", poster_key=None, mime_type="video/mp4", metadata_json={"width": 720, "height": 1280}, production_method="filmed", rights_documentation="Test release", technical_validated=True)
    assets = []
    for letter in ("b", "c"):
        asset = register_media(db, **common, content_hash=letter * 64, storage_key=f"videos/{letter}/demo.mp4")
        review_media(db, asset.id, asset.approval_hash, reviewer="trainer", role="trainer")
        review_media(db, asset.id, asset.approval_hash, reviewer="publisher", role="publisher")
        publish_media(db, asset.id, asset.approval_hash, public_base_url="https://media.example.test", operator="operator", release_id=f"media-{letter}")
        assets.append(asset)
    assert db.get(Exercise, "push_up").published_media["id"] == assets[-1].id
    publish_media(db, assets[0].id, assets[0].approval_hash, public_base_url="https://media.example.test", operator="operator", release_id="rollback", rollback=True)
    assert db.get(Exercise, "push_up").published_media["id"] == assets[0].id
    review_media(db, assets[0].id, assets[0].approval_hash, reviewer="trainer", role="trainer", decision="rejected", comments="Technique needs another take")
    assert db.get(Exercise, "push_up").published_media is None
    with pytest.raises(ValueError, match="approvals required"):
        publish_media(db, assets[0].id, assets[0].approval_hash, public_base_url="https://media.example.test", operator="operator", release_id="blocked-rollback", rollback=True)


def test_legacy_snapshot_preserved_without_fabricated_approval(db):
    exercise = db.get(Exercise, "push_up")
    assert exercise.legacy_snapshot["provenance"] == "legacy_unreviewed"
    assert exercise.legacy_snapshot["id"] == exercise.id
    with pytest.raises(DBAPIError, match="immutable"):
        with db.begin_nested():
            db.execute(text("UPDATE exercises SET legacy_snapshot = '{}'::jsonb WHERE id='push_up'"))


def test_equipment_alias_backfill_preserves_identity(db):
    from app.models.equipment import Equipment
    assert db.get(Equipment, "dumbbells").aliases == ["dumbbell", "db", "dbs"]
    assert db.get(Equipment, "olympic_barbell").name == "Olympic Barbell"
    assert "barbell" in db.get(Equipment, "olympic_barbell").aliases


def test_unknown_source_cannot_become_catalog_content_or_media(db):
    exercise = Exercise(id=f"unknown_{uuid.uuid4().hex}", name="Unknown source", exercise_type="strength",
                        primary_muscle="chest", source="unreviewed_import", secondary_muscles=[])
    db.add(exercise)
    db.flush()
    with pytest.raises(ValueError, match="noncanonical"):
        import_entry(db, entry(id=exercise.id), "operator")
    with pytest.raises(ValueError, match="Canonical"):
        register_media(db, exercise_id=exercise.id, content_hash="e" * 64,
                       storage_key="videos/e/demo.mp4", poster_key=None, mime_type="video/mp4",
                       metadata_json={}, production_method="filmed", rights_documentation="Test-only",
                       technical_validated=True)


def test_reference_only_tracking_is_not_a_program_exercise(db):
    from app.core.workout_template_service import (
        TemplateValidationError, _require_supported_tracking, compatible_substitution,
    )
    revision = import_entry(db, entry(tracking_mode="duration", generation_eligible=False), "author")
    approve(db, revision)
    exercise = publish(db, revision)
    assert get_exercise(db, exercise.id) is not None
    with pytest.raises(TemplateValidationError, match="reference only"):
        _require_supported_tracking(exercise)
    assert not compatible_substitution(db.get(Exercise, "push_up"), exercise)
    _require_supported_tracking(db.get(Exercise, "push_up"))


def test_versioned_review_artifact_replay_does_not_reinstate_rejected_approval(db):
    from app.models.exercise_catalog import ExerciseReview
    from app.schemas.exercise_catalog import ReviewArtifact
    from scripts.exercise_catalog import import_review
    revision = import_entry(db, entry(), "author")
    submit_revision(db, revision.id, revision.content_hash)
    artifact = ReviewArtifact.model_validate({
        "subject_type": "content", "subject_id": revision.id, "subject_hash": revision.content_hash,
        "reviewer": "trainer", "role": "trainer", "decision": "approved",
        "reviewed_at": "2026-09-19T12:00:00Z", "comments": "Test-only review artifact",
    })
    first = import_review(db, artifact)
    review_revision(db, revision.id, revision.content_hash, reviewer="trainer", role="trainer", decision="rejected")
    replay = import_review(db, artifact)
    assert replay["already_imported"] and replay["review_id"] == first["review_id"]
    assert db.query(ExerciseReview).filter_by(revision_id=revision.id).order_by(ExerciseReview.id.desc()).first().decision == "rejected"
    with pytest.raises(ValueError, match="Trainer approval"):
        review_revision(db, revision.id, revision.content_hash, reviewer="publisher", role="publisher", decision="approved")


def test_media_review_cli_records_explicit_reviewer(db, monkeypatch, capsys):
    from contextlib import contextmanager
    from app.models.exercise_catalog import ExerciseMediaReview
    from scripts import exercise_catalog as cli
    asset = register_media(db, exercise_id="push_up", content_hash="d" * 64,
                           storage_key="videos/d/demo.mp4", poster_key=None, mime_type="video/mp4",
                           metadata_json={}, production_method="filmed", rights_documentation="Test rights only",
                           technical_validated=True)
    @contextmanager
    def transactional_session():
        with db.begin_nested():
            yield db
    monkeypatch.setattr(cli, "SessionLocal", transactional_session)
    monkeypatch.setattr(db, "commit", lambda: None)
    monkeypatch.setattr("sys.argv", ["exercise_catalog.py", "media-review", asset.id,
                                   "--hash", asset.approval_hash, "--reviewer", "actual-trainer",
                                   "--role", "trainer", "--decision", "approved", "--comments", "Test-only CLI"])
    cli.main()
    assert '"publication_approved": false' in capsys.readouterr().out
    assert db.query(ExerciseMediaReview).filter_by(asset_id=asset.id).one().reviewer == "actual-trainer"
