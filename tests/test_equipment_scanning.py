import asyncio
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete, func

from app.core import ai_budget_service as budget
from app.core import equipment_scan_service as scans
from app.core.database import SessionLocal, engine, get_db
from app.core.equipment_scan_catalog import SQLCatalogRepository
from app.core.equipment_scan_provider import FixtureRecognitionAdapter, ScanProviderError
from app.core.security.deps import get_current_user
from app.core.settings import settings
from app.main import app
from app.models.equipment_scan import AIRequestReservation, AISpendPeriod, EquipmentScanAttempt, EquipmentScanQuota
from app.models.feature_flags import FeatureFlagConfig
from app.models.subscription import SubscriptionState
from app.models.user import User


def image(color="blue"):
    output = BytesIO()
    with Image.new("RGB", (80, 120), color) as photo:
        photo.save(output, "PNG")
    return output.getvalue()


def adapter(subject="single_equipment", blocked=False):
    return FixtureRecognitionAdapter({"subject": subject, "observations": [{"label": "Lat Pulldown Cable", "equipment_kind": "cable"}] if subject == "single_equipment" else []}, blocked=blocked)


@pytest.fixture
def db(monkeypatch):
    assert settings.APP_ENV == "test" and engine.url.database.startswith("primerep_scan_")
    assert not settings.OPENAI_API_KEY
    with SessionLocal() as session:
        for model in (EquipmentScanAttempt, EquipmentScanQuota, AIRequestReservation, AISpendPeriod):
            session.execute(delete(model))
        row = session.get(FeatureFlagConfig, "test")
        if row is None:
            row = FeatureFlagConfig(environment="test", version=1, subscriptions_purchases_enabled=False, subscriptions_enforcement_enabled=False, updated_at=datetime.now(timezone.utc))
            session.add(row)
        row.equipment_scanning_enabled, row.equipment_scanning_audience = True, "everyone"
        session.commit()
        yield session
        session.rollback()
        config = session.get(FeatureFlagConfig, "test")
        if config:
            config.equipment_scanning_enabled = False
        for model in (EquipmentScanAttempt, EquipmentScanQuota, AIRequestReservation, AISpendPeriod):
            session.execute(delete(model))
        session.query(User).filter(User.email.like("scan-fixture-%@example.test")).delete(synchronize_session=False)
        session.commit()


def user(db, *, verified=True):
    identifier = str(uuid.uuid4())
    row = User(id=identifier, email="scan-fixture-" + identifier + "@example.test", preferred_name="Synthetic", password_hash="not-for-login", has_completed_onboarding=True)
    db.add(row)
    db.flush()
    if verified:
        now = datetime.now(timezone.utc)
        db.add(SubscriptionState(environment="test", user_id=identifier, billing_status="trial", verification_status="verified", verified_at=now, expires_at=now + timedelta(days=1)))
    db.commit()
    return identifier


def run(db, owner, *, key=None, photo=None, provider=None):
    return asyncio.run(scans.scan(db, owner, key or uuid.uuid4(), photo or image(), "image/png", adapter=provider or adapter()))


def test_manual_discovery_and_legacy_guidance_remain_available_with_flag_off(db):
    owner = user(db, verified=False)
    db.get(FeatureFlagConfig, "test").equipment_scanning_enabled = False
    db.commit()
    repo = SQLCatalogRepository(db)
    result = repo.search("Lat Pulldown", 20)
    assert "lat_pulldown_cable" in {item.id for item in result.items}
    assert repo.options("lat_pulldown_cable", []).items[0].exercise_id == "lat_pulldown"
    guidance = repo.guidance("lat_pulldown")
    assert guidance.instructions.status == "instructions_unavailable" and guidance.video.url is None
    with pytest.raises(scans.ScanError) as error:
        run(db, owner)
    assert error.value.detail.code == "scan_disabled" and error.value.detail.attempt_key is None


def test_unverified_access_is_retryable_unknown_not_missing_subscription(db):
    owner = user(db, verified=False)
    with pytest.raises(scans.ScanError) as error:
        run(db, owner)
    assert error.value.status == 503 and error.value.detail.code == "scan_access_unknown"
    assert db.query(EquipmentScanAttempt).count() == 0


def test_same_key_same_image_and_new_key_same_image_do_not_repeat_provider_or_quota(db):
    owner = user(db); provider = adapter(); key = uuid.uuid4()
    first = run(db, owner, provider=provider, key=key)
    replay = run(db, owner, provider=provider, key=key)
    duplicate = run(db, owner, provider=provider)
    assert first.model_dump() == replay.model_dump() == duplicate.model_dump()
    assert provider.calls == ["moderation", "recognition"]
    assert db.query(AIRequestReservation).count() == 1
    assert db.query(EquipmentScanQuota).one().attempts == 1
    with pytest.raises(scans.ScanError) as error:
        run(db, owner, key=key, photo=image("red"), provider=provider)
    assert error.value.detail.code == "scan_idempotency_conflict"
    state = scans.attempt_status(db, owner, key)
    assert state.status == "completed" and state.result.scan_id == first.scan_id


def test_blocked_moderation_consumes_attempt_but_not_recognition_cost(db):
    owner = user(db); provider = adapter(blocked=True)
    with pytest.raises(scans.ScanError) as error:
        run(db, owner, provider=provider)
    assert error.value.detail.code == "scan_image_not_allowed" and error.value.detail.attempt_key
    assert provider.calls == ["moderation"]
    assert db.query(EquipmentScanQuota).one().attempts == 1
    assert db.query(AIRequestReservation).one().accounted_micro_usd == 0
    assert all(item.committed_micro_usd == 0 for item in db.query(AISpendPeriod))


def test_no_match_never_contains_candidates_or_guidance(db):
    owner = user(db)
    result = run(db, owner, provider=adapter("multiple_subjects"))
    assert result.status == "no_match" and result.reason == "multiple_subjects" and result.candidates == []
    assert not result.requires_confirmation


def test_same_account_concurrent_keys_are_fenced_across_sessions_and_return_only_owned_key(db):
    owner = user(db); key = uuid.uuid4(); entered = release = None
    class Held(FixtureRecognitionAdapter):
        async def moderate(self, image, timeout):
            entered.set(); await release.wait()
    provider = Held({"subject": "not_equipment", "observations": []})
    async def exercise():
        nonlocal entered, release
        entered, release = asyncio.Event(), asyncio.Event()
        with SessionLocal() as first, SessionLocal() as second:
            running = asyncio.create_task(scans.scan(first, owner, key, image(), "image/png", adapter=provider))
            await entered.wait()
            with pytest.raises(scans.ScanError) as error:
                await scans.scan(second, owner, uuid.uuid4(), image("red"), "image/png", adapter=adapter())
            assert error.value.detail.code == "scan_in_progress" and error.value.detail.attempt_key == key
            release.set(); await running
    asyncio.run(exercise())
    another = user(db)
    with pytest.raises(scans.ScanError) as error:
        scans.attempt_status(db, another, key)
    assert error.value.status == 404


def test_flag_rollback_after_moderation_stops_recognition_and_releases_known_zero(db):
    owner = user(db); entered = release = None
    class Held(FixtureRecognitionAdapter):
        async def moderate(self, image, timeout):
            self.calls.append("moderation"); entered.set(); await release.wait()
    provider = Held({"subject": "not_equipment", "observations": []})
    async def exercise():
        nonlocal entered, release
        entered, release = asyncio.Event(), asyncio.Event()
        with SessionLocal() as request:
            running = asyncio.create_task(scans.scan(request, owner, uuid.uuid4(), image(), "image/png", adapter=provider))
            await entered.wait()
            db.get(FeatureFlagConfig, "test").equipment_scanning_enabled = False; db.commit()
            release.set()
            with pytest.raises(scans.ScanError) as error:
                await running
            assert error.value.detail.code == "scan_disabled" and error.value.detail.attempt_key
    asyncio.run(exercise())
    assert provider.calls == ["moderation"]
    assert db.query(AIRequestReservation).one().accounted_micro_usd == 0


def test_account_deleted_during_recognition_cannot_persist_or_return_result(db):
    owner = user(db); entered = release = None
    class Held(FixtureRecognitionAdapter):
        async def recognize(self, image, cohort, timeout):
            entered.set(); await release.wait()
            return await super().recognize(image, cohort, timeout)
    provider = Held({"subject": "not_equipment", "observations": []})
    async def exercise():
        nonlocal entered, release
        entered, release = asyncio.Event(), asyncio.Event()
        with SessionLocal() as request:
            running = asyncio.create_task(scans.scan(request, owner, uuid.uuid4(), image(), "image/png", adapter=provider))
            await entered.wait()
            db.execute(delete(User).where(User.id == owner)); db.commit(); release.set()
            with pytest.raises(scans.ScanError): await running
    asyncio.run(exercise())
    assert db.query(EquipmentScanAttempt).filter_by(user_id=owner).count() == 0
    assert db.query(EquipmentScanQuota).filter_by(user_id=owner).count() == 0


def test_unknown_provider_charge_stays_reserved_without_blocking_other_accounts_globally(db):
    class Unknown(FixtureRecognitionAdapter):
        async def recognize(self, image, cohort, timeout):
            raise ScanProviderError("scan_provider_outcome_unknown")
    owners = [user(db) for _ in range(3)]
    for owner in owners[:2]:
        with pytest.raises(scans.ScanError): run(db, owner, provider=Unknown({"subject": "not_equipment", "observations": []}))
    assert all(item.status == "unknown" and item.accounted_micro_usd is None for item in db.query(AIRequestReservation))
    result = run(db, owners[2])
    assert result.status == "candidates"
    with pytest.raises(scans.ScanError) as error: run(db, owners[0], photo=image("red"))
    assert error.value.detail.code == "scan_provider_outcome_unknown"


def test_daily_quota_survives_receipt_expiry_and_prune(db, monkeypatch):
    owner = user(db)
    monkeypatch.setattr(settings, "EQUIPMENT_SCAN_ATTEMPTS_PER_MINUTE", 100)
    for index in range(20): run(db, owner, photo=image((index * 10, 50, 100)))
    now = datetime.now(timezone.utc)
    deleted, _ = scans.prune(db, now=now + timedelta(minutes=16))
    assert deleted == 20 and db.query(EquipmentScanQuota).one().attempts == 20
    with pytest.raises(scans.ScanError) as error: run(db, owner, photo=image("green"))
    assert error.value.detail.code == "scan_quota_exceeded"


def test_shared_budget_admission_is_atomic_and_unknown_settlement_keeps_full_ceiling(db, monkeypatch):
    db.commit()
    for field in ("OPENAI_TEST_BUDGET_USD", "OPENAI_DAILY_BUDGET_USD", "OPENAI_MONTHLY_BUDGET_USD"):
        monkeypatch.setattr(settings, field, "0.30")
    def reserve():
        try: return budget.reserve("coach_week", "gpt-6-luna", 1_050_000, 2000)
        except budget.AIBudgetUnavailable: return None
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(lambda _: reserve(), range(2)))
    admitted = [item for item in results if item]
    assert len(admitted) == 1
    budget.settle(admitted[0])
    db.expire_all()
    assert db.query(AIRequestReservation).one().status == "unknown"
    assert all(item.committed_micro_usd == admitted[0].maximum_micro_usd for item in db.query(AISpendPeriod))
    with pytest.raises(budget.AIBudgetUnavailable): budget.reserve("equipment_scan", "gpt-6-luna", 1_050_000, 768)


def test_routes_emit_strict_recovery_errors_without_raw_uploads_and_keep_manual_search(db, monkeypatch):
    owner = user(db)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "automated-not-live")
    provider = adapter()
    monkeypatch.setattr(scans, "OpenAIRecognitionAdapter", lambda: provider)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner)
    try:
        with TestClient(app) as client:
            assert client.get("/v1/equipment/search?q=Lat").status_code == 200
            assert client.get("/v1/exercises/lat_pulldown/guidance").json()["instructions"]["status"] == "instructions_unavailable"
            key = str(uuid.uuid4())
            response = client.post("/v1/equipment-scans", content=image(), headers={"Content-Type": "image/png", "Idempotency-Key": key})
            assert response.status_code == 200, response.text
            recovery = client.get("/v1/equipment-scans/attempts/" + key)
            assert recovery.status_code == 200 and recovery.json()["result"] == response.json()
            malformed = client.post("/v1/equipment-scans", content=b"PRIVATE_IMAGE_CANARY", headers={"Content-Type": "image/png", "Idempotency-Key": str(uuid.uuid4())})
            assert malformed.status_code == 422
            assert "PRIVATE_IMAGE_CANARY" not in malformed.text
            assert malformed.json()["error"]["attempt_key"] is None
            assert "retry_after_seconds" in malformed.json()["error"]
    finally:
        app.dependency_overrides.clear()


def test_expired_old_positive_is_unknown_until_fresh_negative_verification(db, monkeypatch):
    from app.core import revenuecat_client
    owner = user(db); now = datetime.now(timezone.utc)
    state = db.get(SubscriptionState, ("test", owner))
    state.expires_at = now - timedelta(minutes=5)
    state.verified_at = now - timedelta(hours=1)
    state.billing_status = "active"
    db.commit()
    monkeypatch.setattr(revenuecat_client, "configured", lambda: True)
    with pytest.raises(scans.ScanError) as error: scans.require_scan_access(db, owner, now=now)
    assert error.value.status == 503 and error.value.detail.code == "scan_access_unknown"
    state = db.get(SubscriptionState, ("test", owner))
    state.verified_at = now
    state.billing_status = "expired"
    db.commit()
    with pytest.raises(scans.ScanError) as error: scans.require_scan_access(db, owner, now=now)
    assert error.value.status == 403 and error.value.detail.code == "scan_access_required"


def test_cpu_normalization_yields_the_event_loop(db, monkeypatch):
    import threading
    import time
    owner = user(db); entered = threading.Event(); release = threading.Event()
    original = scans.normalize_image
    def slow(raw, mime):
        entered.set(); release.wait(1); return original(raw, mime)
    monkeypatch.setattr(scans, "normalize_image", slow)
    async def exercise():
        with SessionLocal() as request:
            started = time.monotonic()
            task = asyncio.create_task(scans.scan(request, owner, uuid.uuid4(), image(), "image/png", adapter=adapter()))
            await asyncio.sleep(0.04)
            assert entered.is_set() and time.monotonic() - started < 0.5
            release.set(); await task
    asyncio.run(exercise())


def test_slow_upload_has_no_open_db_transaction_and_times_out_before_provider(db, monkeypatch):
    from app.api.v1 import equipment_scans as routes
    owner = user(db); calls = []
    class SlowRequest:
        query_params = {}
        headers = {"Content-Type": "image/png", "Idempotency-Key": str(uuid.uuid4())}
        async def stream(self):
            assert not db.in_transaction()
            yield b"partial"
            await asyncio.sleep(10)
    async def provider(*args): calls.append("provider")
    monkeypatch.setattr(routes, "scan", provider)
    monkeypatch.setattr(routes, "UPLOAD_TIMEOUT_SECONDS", 0.02)
    with pytest.raises(scans.ScanError) as error:
        asyncio.run(routes.create_equipment_scan(SlowRequest(), SimpleNamespace(id=owner), db))
    assert error.value.detail.code == "scan_invalid_request" and error.value.detail.attempt_key is None
    assert calls == [] and db.query(EquipmentScanAttempt).count() == 0


def test_manual_pages_bind_versions_and_missing_attachments_have_owned_names(db):
    from app.core.equipment_scan_catalog import CatalogError
    from app.models.equipment import Equipment
    from app.models.exercise import Exercise
    repo = SQLCatalogRepository(db)
    first = repo.search("", 1)
    second = repo.search("", 1, first.next_cursor)
    assert first.items[0].id != second.items[0].id
    equipment = db.get(Equipment, first.items[0].id)
    equipment.name += " changed"
    db.flush()
    with pytest.raises(CatalogError) as error: repo.search("", 1, first.next_cursor)
    assert error.value.code == "scan_result_stale"
    exercise = db.get(Exercise, "lat_pulldown")
    attachment = db.get(Equipment, "dumbbell") or db.query(Equipment).filter(Equipment.id != "lat_pulldown_cable").first()
    exercise.equipment.append(attachment); db.flush()
    option = repo.options("lat_pulldown_cable", []).items[0]
    assert option.compatibility == "needs_equipment_confirmation"
    assert option.missing_equipment[0].id == attachment.id and option.missing_equipment[0].name == attachment.name
    assert repo.options("lat_pulldown_cable", [attachment.id]).items[0].compatibility == "compatible"


def test_guidance_checks_current_content_and_media_reviews_without_legacy_fallback(db):
    from test_exercise_catalog import entry, approve, publish
    from app.core.exercise_catalog_service import import_entry, register_media, review_media, publish_media, review_revision
    from app.core.equipment_scan_catalog import CatalogError
    repo = SQLCatalogRepository(db)
    payload = entry(equipment_ids=["lat_pulldown_cable"], aliases=[])
    revision = import_entry(db, payload, "fixture-author")
    with pytest.raises(CatalogError): repo.guidance(payload.id)
    approve(db, revision); exercise = publish(db, revision)
    assert repo.guidance(exercise.id).instructions.status == "published"
    assert len(repo.options("lat_pulldown_cable", []).items) == 2
    assert repo.guidance(exercise.id).video.status == "video_unavailable"
    values = dict(exercise_id=exercise.id, content_hash="a" * 64, storage_key="fixture/video.mp4", poster_key=None, mime_type="video/mp4", metadata_json={}, production_method="filmed", rights_documentation="Synthetic test-only rights fixture", technical_validated=True)
    asset = register_media(db, **values)
    review_media(db, asset.id, asset.approval_hash, reviewer="fixture-trainer", role="trainer")
    review_media(db, asset.id, asset.approval_hash, reviewer="fixture-publisher", role="publisher")
    publish_media(db, asset.id, asset.approval_hash, public_base_url="https://fixture.example.test", operator="fixture-operator", release_id="fixture-only")
    assert repo.guidance(exercise.id).video.status == "approved"
    review_media(db, asset.id, asset.approval_hash, reviewer="fixture-trainer", role="trainer", decision="rejected")
    assert repo.guidance(exercise.id).video.status == "video_unavailable"
    review_revision(db, revision.id, revision.content_hash, reviewer="trainer@example.test", role="trainer", decision="rejected")
    assert repo.guidance(exercise.id).instructions.status == "instructions_unavailable"
