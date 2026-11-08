import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine, delete, event as sqlalchemy_event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.v1.workout_diagnostics import router
from app.core.database import get_db
from app.core.rate_limit import user_limiter
from app.core.security.deps import get_current_user
from app.core.security.jwt import create_access_token
from app.core.settings import settings
from app.core.workout_diagnostics_report import diagnostic_report
from app.core.workout_diagnostics_service import export_diagnostics, ingest_diagnostics, prune_diagnostics
from app.models.feature_flags import FeatureFlagConfig, FeatureFlagUserOverride
from app.models.user import User
from app.models.workout_diagnostics import WorkoutDiagnostic as Raw, WorkoutDiagnosticAggregate as Aggregate
from app.schemas.workout_diagnostics import WorkoutDiagnosticBatch

NOW = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
OWNER, OTHER = str(uuid4()), str(uuid4())


def diagnostic(kind="local_save", **fields):
    defaults = {"outcome": "success", "saved_count": 1} if kind == "local_save" else {}
    if kind in {"operation_queued", "sync_attempt", "sync_result", "retry_scheduled", "operation_terminal"}:
        defaults.update(trace_id=str(uuid4()), operation_type="set")
    return {"event_id": str(uuid4()), "event_type": kind, "occurred_at_ms": int(NOW.timestamp() * 1000), **defaults, **fields}


def batch(*events):
    return WorkoutDiagnosticBatch.model_validate({"schema_version": 1, "platform": "ios", "app_version": "1.2.3", "events": list(events) or [diagnostic()]})


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'diagnostics.db'}", connect_args={"check_same_thread": False})

    @sqlalchemy_event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    for model in (User, FeatureFlagConfig, FeatureFlagUserOverride, Raw, Aggregate):
        model.__table__.create(engine)
    monkeypatch.setattr(settings, "APP_ENV", "test")
    with Session(engine) as session:
        session.add_all([User(id=value, email=f"{value}@example.test", preferred_name="Test", password_hash="test") for value in (OWNER, OTHER)])
        session.add(FeatureFlagConfig(environment="test", version=1, updated_at=NOW, workout_reliability_monitoring_enabled=True))
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture
def client(db):
    app = FastAPI()
    app.state.limiter = user_limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {create_access_token(OWNER)}"
        yield client


def ingest(db, *events, user_id=OWNER, now=NOW):
    result = ingest_diagnostics(db, user_id, batch(*events), now=now)
    db.commit()
    return result


def test_authenticated_endpoint_is_independent_of_paid_access(client, db):
    result = client.post("/v1/diagnostics/workout-sync", json=batch().model_dump(mode="json"))
    assert result.status_code == 200, result.text
    assert result.json() == {"enabled": True, "accepted": 1, "duplicates": 0, "discarded": 0}
    assert db.scalar(select(Raw)).user_id == OWNER
    client.headers.pop("Authorization")
    assert client.post("/v1/diagnostics/workout-sync", json=batch().model_dump(mode="json")).status_code == 401


def test_disabled_and_targeted_flags_discard_without_storage(client, db):
    config = db.get(FeatureFlagConfig, "test")
    config.workout_reliability_monitoring_audience = "selected"
    db.commit()
    payload = batch().model_dump(mode="json")
    assert client.post("/v1/diagnostics/workout-sync", json=payload).json() == {"enabled": False, "accepted": 0, "duplicates": 0, "discarded": 1}
    assert db.scalar(select(func.count()).select_from(Raw)) == 0
    assert db.scalar(select(func.count()).select_from(Aggregate)) == 0
    db.add(FeatureFlagUserOverride(environment="test", user_id=OWNER, workout_reliability_monitoring_enabled=True))
    db.commit()
    assert client.post("/v1/diagnostics/workout-sync", json=payload).json()["accepted"] == 1
    config.workout_reliability_monitoring_enabled = False
    db.commit()
    assert client.post("/v1/diagnostics/workout-sync", json=payload).json()["enabled"] is False


@pytest.mark.parametrize("mutation", [
    lambda body: body.update(environment="production"),
    lambda body: body.update(user_id=OTHER),
    lambda body: body["events"][0].update(weight="PRIVATE_MARKER"),
    lambda body: body["events"][0].update(error_category="PRIVATE_MARKER"),
    lambda body: body["events"][0].update(event_id="PRIVATE_MARKER"),
    lambda body: body["events"].extend([diagnostic()] * 20),
    lambda body: body["events"][0].update(saved_count=0),
])
def test_invalid_payload_is_sanitized_and_never_stored(client, db, caplog, mutation):
    payload = batch().model_dump(mode="json")
    mutation(payload)
    response = client.post("/v1/diagnostics/workout-sync", json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid diagnostic batch"}
    assert "PRIVATE_MARKER" not in caplog.text
    assert db.scalar(select(func.count()).select_from(Raw)) == 0


def test_body_limit_counts_stream_without_trusting_length(client, db):
    body = json.dumps(batch().model_dump(mode="json")).encode()
    assert client.post("/v1/diagnostics/workout-sync", content=body + b" " * (32768 - len(body)), headers={"Content-Length": "1"}).status_code == 200
    response = client.post("/v1/diagnostics/workout-sync", content=iter((body, b" " * 32768)))
    assert response.status_code == 413
    assert client.post("/v1/diagnostics/workout-sync", content=b'{"private":').json() == {"detail": "Invalid diagnostic batch"}
    assert client.post("/v1/diagnostics/workout-sync?user_id=PRIVATE_MARKER", content=body).json() == {"detail": "Diagnostics do not accept query selectors"}


def test_rate_limit_is_per_authenticated_account_without_identity_logs(client, caplog):
    payload = batch().model_dump(mode="json")
    token = client.headers["Authorization"]
    for _ in range(30):
        assert client.post("/v1/diagnostics/workout-sync", json=payload).status_code == 200
    assert client.post("/v1/diagnostics/workout-sync", json=payload).status_code == 429
    for canary in (OWNER, f"{OWNER}@example.test", token):
        assert canary not in caplog.text
    assert client.post("/v1/diagnostics/workout-sync", json=payload, headers={"Authorization": f"Bearer {create_access_token(OTHER)}"}).status_code == 200


def test_database_failure_has_no_payload_in_response_or_logs(client, db, monkeypatch, caplog):
    def unavailable(*args, **kwargs):
        raise OperationalError("PRIVATE_MARKER", {}, Exception("PRIVATE_MARKER"))

    monkeypatch.setattr(db, "commit", unavailable)
    result = client.post("/v1/diagnostics/workout-sync", json=batch().model_dump(mode="json"))
    assert result.status_code == 503
    assert result.json() == {"detail": "Diagnostics are temporarily unavailable"}
    assert "PRIVATE_MARKER" not in caplog.text
    assert db.scalar(select(func.count()).select_from(Raw)) == 0


def test_exact_and_logical_terminal_dedup_are_owned_and_environment_scoped(db, monkeypatch):
    terminal = diagnostic("operation_terminal", outcome="acknowledged", acknowledgment_kind="direct", queue_age_ms=1200)
    assert ingest(db, terminal)["accepted"] == 1
    assert ingest(db, terminal)["duplicates"] == 1
    assert ingest(db, {**terminal, "event_id": str(uuid4()), "outcome": "superseded", "acknowledgment_kind": None})["duplicates"] == 1
    assert ingest(db, terminal, user_id=OTHER)["accepted"] == 1
    db.add(FeatureFlagConfig(environment="preview", version=1, updated_at=NOW, workout_reliability_monitoring_enabled=True))
    db.commit()
    monkeypatch.setattr(settings, "APP_ENV", "preview")
    assert ingest(db, terminal)["accepted"] == 1
    assert db.scalar(select(func.count()).select_from(Raw)) == 3
    assert diagnostic_report(db, now=NOW)["terminal_outcomes"]["acknowledged"] == 1


@pytest.mark.parametrize("offset", [-7 * 86400000 - 1, 300001])
def test_invalid_clock_retains_real_epoch_but_removes_age(db, offset):
    event = diagnostic("operation_terminal", outcome="acknowledged", acknowledgment_kind="reconciled", queue_age_ms=321,
                       occurred_at_ms=int(NOW.timestamp() * 1000) + offset)
    ingest(db, event)
    raw = db.scalar(select(Raw))
    assert raw.payload["occurred_at_ms"] == event["occurred_at_ms"]
    assert raw.payload["clock_invalid"] is True
    assert "queue_age_ms" not in raw.payload
    report = diagnostic_report(db, now=NOW)
    assert report["queue_to_ack_age"] == {"known_samples": 0, "mean_ms": None, "histogram": []}
    assert report["coverage"]["acknowledged_with_unknown_age"] == 1


def test_request_failure_is_nonterminal_and_later_ack_recovers(db):
    trace = str(uuid4())
    ingest(db, diagnostic("operation_queued", trace_id=trace), diagnostic("sync_attempt", trace_id=trace),
           diagnostic("sync_result", trace_id=trace, outcome="failure", error_category="network"))
    assert diagnostic_report(db, now=NOW)["trace_cohort"]["pending_or_unresolved"] == 1
    ingest(db, diagnostic("retry_scheduled", trace_id=trace, error_category="network", retry_delay_ms=1000),
           diagnostic("operation_terminal", trace_id=trace, outcome="acknowledged", acknowledgment_kind="reconciled", recovered_after_restart=True, queue_age_ms=4000))
    report = diagnostic_report(db, now=NOW)
    assert report["trace_cohort"]["acknowledged"] == 1
    assert report["write_requests"]["attempts"] == 1
    assert report["write_requests"]["failures"] == 1
    assert report["terminal_outcomes"]["acknowledged"] == 1
    assert report["queue_to_ack_age"]["mean_ms"] == 4000


def test_report_denominators_finish_stages_and_nonadditive_gauges(db):
    ingest(db, diagnostic(saved_count=3, duration_ms=8), diagnostic(outcome="failure", saved_count=None, error_category="storage", duration_ms=30),
           diagnostic(outcome="failure", saved_count=None, error_category="validation"),
           diagnostic(outcome="failure", saved_count=None, error_category="admission_conflict"),
           diagnostic("finish_result", outcome="success", finish_stage="server", duration_ms=100),
           diagnostic("finish_result", outcome="failure", finish_stage="local_cleanup", error_category="storage"),
           diagnostic("diagnostics_dropped", dropped_count=7))
    old_ms = int((NOW - timedelta(minutes=6)).timestamp() * 1000)
    ingest(db, diagnostic("queue_snapshot", queue_depth=100, unknown_age_count=90, occurred_at_ms=old_ms), now=NOW - timedelta(minutes=6))
    ingest(db, diagnostic("queue_snapshot", queue_depth=3, unknown_age_count=1))
    ingest(db, diagnostic("queue_snapshot", queue_depth=4, occurred_at_ms=old_ms), user_id=OTHER, now=NOW - timedelta(minutes=6))
    report = diagnostic_report(db, now=NOW)
    assert report["local_writes"]["storage_outcome_denominator"] == 2
    assert report["local_writes"]["storage_failure_share"] == 0.5
    assert report["local_writes"]["saved_sets"] == 3
    assert report["queue_gauges"]["latest_observed_depth"] == 7
    assert report["queue_gauges"]["fresh_observed_depth"] == 3
    assert report["queue_gauges"]["stale_sampled_accounts"] == 1
    assert report["finish"]["server"]["successes"] == 1
    assert report["finish"]["local_cleanup"]["failures"] == 1
    assert report["coverage"]["dropped_diagnostics"] == 7
    encoded = json.dumps(report)
    assert OWNER not in encoded and OTHER not in encoded
    for row in db.scalars(select(Raw)):
        assert row.event_id not in encoded
    assert "user_id" not in Aggregate.__table__.columns
    assert "trace_id" not in Aggregate.__table__.columns


def test_delayed_snapshot_is_not_reported_as_fresh(db):
    ingest(db, diagnostic("queue_snapshot", queue_depth=8, occurred_at_ms=int((NOW - timedelta(days=1)).timestamp() * 1000)))
    gauges = diagnostic_report(db, now=NOW)["queue_gauges"]
    assert gauges["stale_sampled_accounts"] == 1
    assert gauges.get("fresh_sampled_accounts", 0) == 0


def test_batched_snapshots_use_observed_time_not_uuid_sort_order(db):
    older = diagnostic("queue_snapshot", event_id="ffffffff-ffff-4fff-8fff-ffffffffffff", queue_depth=9,
                       occurred_at_ms=int(NOW.timestamp() * 1000) - 1000)
    newer = diagnostic("queue_snapshot", event_id="00000000-0000-4000-8000-000000000001", queue_depth=2)
    ingest(db, newer, older)
    gauges = diagnostic_report(db, now=NOW)["queue_gauges"]
    assert gauges["fresh_observed_depth"] == 2
    assert gauges["sampled_accounts"] == 1


def test_ambiguous_or_invalid_clock_snapshots_are_unknown(db):
    ingest(db, diagnostic("queue_snapshot", queue_depth=8), diagnostic("queue_snapshot", queue_depth=3))
    ingest(db, diagnostic("queue_snapshot", queue_depth=4, clock_invalid=True), user_id=OTHER)
    gauges = diagnostic_report(db, now=NOW)["queue_gauges"]
    assert gauges["unknown_sampled_accounts"] == 2
    assert gauges["fresh_observed_depth"] is None
    assert gauges["latest_observed_depth"] is None


def test_retention_export_deletion_and_read_only_report(db, monkeypatch):
    old = diagnostic()
    ingest(db, old, now=NOW - timedelta(days=15))
    ingest(db, diagnostic(), now=NOW - timedelta(days=91))
    recent = diagnostic()
    ingest(db, recent)
    ingest(db, diagnostic(), user_id=OTHER)
    before = db.scalar(select(func.count()).select_from(Raw))
    diagnostic_report(db, now=NOW, days=90)
    assert db.scalar(select(func.count()).select_from(Raw)) == before
    retained = list(export_diagnostics(db, OWNER))
    assert len(retained) == 1 and retained[0]["event_id"] == recent["event_id"]
    assert "user_id" not in retained[0]
    result = prune_diagnostics(db, now=NOW)
    db.commit()
    assert result["aggregate_buckets_deleted"] > 0
    assert db.scalar(select(func.count()).select_from(Raw)) == 2
    db.execute(delete(User).where(User.id == OWNER))
    db.commit()
    assert list(export_diagnostics(db, OWNER)) == []
    assert db.scalar(select(func.count()).select_from(Raw)) == 1
    assert db.scalar(select(func.count()).select_from(Aggregate)) > 0


def test_pruning_retention_boundaries_and_other_environment_isolation(db):
    for days in (90, 89):
        then = NOW - timedelta(days=days)
        ingest(db, diagnostic(occurred_at_ms=int(then.timestamp() * 1000)), now=then)
    cutoff = NOW - timedelta(days=14)
    for environment, received_at in (("test", cutoff), ("test", cutoff - timedelta(microseconds=1)), ("preview", NOW - timedelta(days=20))):
        entry = diagnostic()
        db.add(Raw(environment=environment, user_id=OWNER, event_id=entry["event_id"], event_type="local_save",
                   received_at=received_at, platform="ios", app_version="1.0.0", payload=entry))
    source = db.scalar(select(Aggregate))
    values = {column.name: getattr(source, column.name) for column in Aggregate.__table__.columns if column.name != "id"}
    db.add(Aggregate(**{**values, "environment": "preview", "bucket_date": NOW.date() - timedelta(days=91)}))
    db.commit()
    prune_diagnostics(db, now=NOW)
    db.commit()
    retained = db.scalars(select(Raw)).all()
    assert len(retained) == 2
    assert {row.environment for row in retained} == {"test", "preview"}
    assert db.scalar(select(Raw.received_at).where(Raw.environment == "test")) == cutoff.replace(tzinfo=None)
    assert set(db.scalars(select(Aggregate.bucket_date).where(Aggregate.environment == "test"))) == {NOW.date() - timedelta(days=89)}
    assert db.scalar(select(func.count()).select_from(Aggregate).where(Aggregate.environment == "preview")) == 1


@pytest.mark.parametrize("days", [0, 91, True, "7"])
def test_report_rejects_unbounded_windows(db, days):
    with pytest.raises(ValueError):
        diagnostic_report(db, days=days)
