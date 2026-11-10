import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zipfile import ZipFile

from alembic import command
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from app.core.export_service import create_export
from app.core.settings import settings
from app.core.user_service import delete_user
from app.core.workout_diagnostics_report import diagnostic_report
from app.core.workout_diagnostics_service import ingest_diagnostics
from app.models.feature_flags import FeatureFlagConfig
from app.models.user import User
from app.models.workout_diagnostics import WorkoutDiagnostic as Raw, WorkoutDiagnosticAggregate as Aggregate
from app.schemas.workout_diagnostics import WorkoutDiagnosticBatch
from test_subscription_migrations import migration_database


def event(kind="local_save", **values):
    return {"event_id": str(uuid4()), "event_type": kind, "occurred_at_ms": int(datetime.now(timezone.utc).timestamp() * 1000), **values}


def payload(value):
    return WorkoutDiagnosticBatch.model_validate({"schema_version": 1, "platform": "ios", "app_version": "1.0.0", "events": [value]})


def seed(engine):
    owner, other = str(uuid4()), str(uuid4())
    with Session(engine) as db:
        db.add_all([User(id=value, email=f"{value}@example.test", preferred_name="Observer", password_hash="test") for value in (owner, other)])
        db.add(FeatureFlagConfig(environment="test", version=1, updated_at=datetime.now(timezone.utc), workout_reliability_monitoring_enabled=True))
        db.commit()
    return owner, other


def test_real_migration_constraints_concurrency_and_preservation(migration_database, monkeypatch):
    config, engine = migration_database
    monkeypatch.setattr(settings, "APP_ENV", "test")
    command.upgrade(config, "g8h9i0j1k2l3")
    owner, other = seed(engine)
    command.upgrade(config, "head")
    schema = inspect(engine)
    assert {"workout_diagnostics", "workout_diagnostic_aggregates"} <= set(schema.get_table_names())
    indexes = {index["name"]: index for index in schema.get_indexes("workout_diagnostics")}
    assert indexes["uq_workout_diagnostic_terminal"]["unique"] is True
    assert "user_id" not in {column["name"] for column in schema.get_columns("workout_diagnostic_aggregates")}

    def concurrent(values):
        barrier = threading.Barrier(len(values))

        def write(value):
            with Session(engine) as db:
                barrier.wait(timeout=10)
                receipt = ingest_diagnostics(db, owner, payload(value))
                db.commit()
                return receipt

        with ThreadPoolExecutor(max_workers=len(values)) as workers:
            return list(workers.map(write, values))

    local = event(outcome="success", saved_count=1, duration_ms=4)
    results = concurrent([local] * 4)
    assert sum(row["accepted"] for row in results) == 1
    assert sum(row["duplicates"] for row in results) == 3
    trace = str(uuid4())
    results = concurrent([event("operation_terminal", trace_id=trace, operation_type="set", outcome="acknowledged", acknowledgment_kind="direct", queue_age_ms=500) for _ in range(4)])
    assert sum(row["accepted"] for row in results) == 1
    assert sum(row["duplicates"] for row in results) == 3
    results = concurrent([event(outcome="success", saved_count=1, duration_ms=4) for _ in range(8)])
    assert sum(row["accepted"] for row in results) == 8
    with Session(engine) as db:
        report = diagnostic_report(db)
        assert report["local_writes"]["successful_attempts"] == 9
        assert report["local_writes"]["saved_sets"] == 9
        assert report["terminal_outcomes"]["acknowledged"] == 1
        assert db.scalar(select(func.count()).select_from(Raw)) == 10
        older = event("queue_snapshot", event_id="ffffffff-ffff-4fff-8fff-ffffffffffff", queue_depth=8,
                      occurred_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000) - 1000)
        newer = event("queue_snapshot", event_id="00000000-0000-4000-8000-000000000001", queue_depth=2)
        snapshots = payload(older).model_copy(update={"events": [payload(newer).events[0], payload(older).events[0]]})
        ingest_diagnostics(db, owner, snapshots)
        db.commit()
        assert diagnostic_report(db)["queue_gauges"]["fresh_observed_depth"] == 2
    command.downgrade(config, "g8h9i0j1k2l3")
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM users WHERE id IN (:owner, :other)"), {"owner": owner, "other": other}) == 2
        assert conn.scalar(text("SELECT workout_reliability_monitoring_enabled FROM feature_flag_configs WHERE environment='test'")) is True
    assert "workout_diagnostics" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")


def test_real_export_account_deletion_and_executable_pruning(migration_database, monkeypatch):
    config, engine = migration_database
    monkeypatch.setattr(settings, "APP_ENV", "test")
    monkeypatch.setenv("APP_ENV", "test")
    command.upgrade(config, "head")
    owner, other = seed(engine)
    own = event(outcome="success", saved_count=1)
    foreign = event(outcome="success", saved_count=1)
    with Session(engine) as db:
        ingest_diagnostics(db, owner, payload(own))
        ingest_diagnostics(db, other, payload(foreign))
        db.commit()
    artifact = create_export(engine, owner, 0, "KG")
    try:
        with ZipFile(artifact.path) as archive:
            account = json.loads(archive.read("account.json"))
        assert [row["event_id"] for row in account["workout_diagnostics"]] == [own["event_id"]]
        assert foreign["event_id"] not in json.dumps(account)
        assert "user_id" not in account["workout_diagnostics"][0]
    finally:
        artifact.cleanup()
    with Session(engine) as db:
        delete_user(db, db.get(User, owner))
        assert db.scalar(select(func.count()).select_from(Raw)) == 1
        assert db.scalar(select(Raw.user_id)) == other
        assert db.scalar(select(func.count()).select_from(Aggregate)) > 0
        db.query(Raw).update({Raw.received_at: datetime.now(timezone.utc) - timedelta(days=15)})
        db.query(Aggregate).update({Aggregate.bucket_date: datetime.now(timezone.utc).date() - timedelta(days=91)})
        db.commit()
    report = subprocess.run([sys.executable, "-m", "scripts.workout_diagnostics", "report", "--days", "90"], check=True, capture_output=True, text=True)
    assert json.loads(report.stdout)["observed_only"] is True
    assert owner not in report.stdout and other not in report.stdout
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Raw)) == 1
    prune = subprocess.run([sys.executable, "-m", "scripts.workout_diagnostics", "prune"], check=True, capture_output=True, text=True)
    assert json.loads(prune.stdout)["raw_deleted"] == 1
    assert json.loads(prune.stdout)["aggregate_buckets_deleted"] > 0
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Raw)) == 0
        assert db.scalar(select(func.count()).select_from(Aggregate)) == 0
