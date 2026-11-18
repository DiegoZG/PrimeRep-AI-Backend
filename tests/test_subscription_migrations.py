import ast
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from migration_helpers import ensure_feature_flags, ensure_flag_targeting, ensure_session_arrangements

ROOT = Path(__file__).resolve().parents[1]


def test_revision_graph_has_unique_ids_and_one_head():
    ids = []
    for path in (ROOT / "alembic/versions").glob("*.py"):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "revision" for target in node.targets):
                ids.append(ast.literal_eval(node.value))
    assert len(ids) == len(set(ids))
    assert ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads() == ["i0j1k2l3m4n5"]


def test_monitoring_flag_upgrade_and_downgrade_preserve_existing_rules(migration_database):
    config, engine = migration_database
    command.upgrade(config, "f7g8h9i0j1k2")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO users (id, email, preferred_name, password_hash, has_completed_onboarding, subscription_tier, coach_insights_enabled) VALUES ('monitoring-old', 'old@example.test', 'Old', 'test', true, 'free', false), ('monitoring-only', 'only@example.test', 'Only', 'test', true, 'free', false)"))
        connection.execute(text("INSERT INTO feature_flag_configs (environment, version, subscriptions_purchases_enabled, subscriptions_enforcement_enabled, subscriptions_onboarding_enabled, launch_locked, updated_at) VALUES ('test', 7, false, false, false, false, now())"))
        connection.execute(text("INSERT INTO feature_flag_user_overrides (environment, user_id, subscriptions_purchases_enabled) VALUES ('test', 'monitoring-old', true)"))
        connection.execute(text("INSERT INTO feature_flag_audits (environment, version, changed_at, scope, before, after) VALUES ('test', 7, now(), 'global', '{}', '{}')"))
    command.upgrade(config, "head")
    with engine.begin() as connection:
        row = connection.execute(text("SELECT version, workout_reliability_monitoring_enabled, workout_reliability_monitoring_audience FROM feature_flag_configs WHERE environment='test'")).one()
        assert tuple(row) == (7, False, "everyone")
        assert connection.scalar(text("SELECT COUNT(*) FROM feature_flag_audits")) == 1
        assert connection.scalar(text("SELECT subscriptions_purchases_enabled FROM feature_flag_user_overrides WHERE user_id='monitoring-old'")) is True
        connection.execute(text("INSERT INTO feature_flag_user_overrides (environment, user_id, workout_reliability_monitoring_enabled) VALUES ('test', 'monitoring-only', true)"))
    command.downgrade(config, "f7g8h9i0j1k2")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM feature_flag_user_overrides WHERE user_id='monitoring-only'")) == 0
        assert connection.scalar(text("SELECT subscriptions_purchases_enabled FROM feature_flag_user_overrides WHERE user_id='monitoring-old'")) is True
        assert connection.scalar(text("SELECT COUNT(*) FROM feature_flag_audits")) == 1
    command.upgrade(config, "head")


def test_onboarding_upgrade_preserves_existing_billing_accounts(migration_database):
    config, engine = migration_database
    command.upgrade(config, "e6f7g8h9i0j1")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO users (id, email, preferred_name, password_hash, has_completed_onboarding, subscription_tier, coach_insights_enabled) VALUES ('existing-billing', 'existing@example.com', 'Existing', 'test', true, 'free', false)"))
        connection.execute(text("INSERT INTO feature_flag_configs (environment, version, subscriptions_purchases_enabled, subscriptions_enforcement_enabled, launch_locked, updated_at) VALUES ('test', 7, false, false, false, now())"))
        connection.execute(text("INSERT INTO subscription_states (environment, user_id, billing_status, verification_status) VALUES ('test', 'existing-billing', 'expired', 'verified')"))
        connection.execute(text("INSERT INTO feature_flag_user_overrides (environment, user_id, subscriptions_purchases_enabled) VALUES ('test', 'existing-billing', true)"))
    command.upgrade(config, "head")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT subscriptions_onboarding_enabled FROM feature_flag_configs WHERE environment='test'")) is False
        assert connection.scalar(text("SELECT version FROM feature_flag_configs WHERE environment='test'")) == 7
        assert connection.scalar(text("SELECT onboarding_enrolled_at FROM subscription_states WHERE user_id='existing-billing'")) is None
        connection.execute(text("UPDATE feature_flag_user_overrides SET subscriptions_purchases_enabled=NULL, subscriptions_onboarding_enabled=true WHERE user_id='existing-billing'"))
        assert connection.scalar(text("SELECT subscriptions_onboarding_enabled FROM feature_flag_user_overrides WHERE user_id='existing-billing'")) is True


@pytest.fixture
def migration_database(monkeypatch):
    base = os.getenv("SUBSCRIPTION_MIGRATION_TEST_URL")
    if not base:
        pytest.skip("Set SUBSCRIPTION_MIGRATION_TEST_URL for isolated PostgreSQL migration snapshots")
    engine = create_engine(base, isolation_level="AUTOCOMMIT")
    database = f"billing_migration_{uuid4().hex}"
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database}"'))
    url = make_url(base).set(database=database)
    target_url = url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", target_url)
    config = Config(str(ROOT / "alembic.ini"))
    target = create_engine(target_url)
    try:
        yield config, target
    finally:
        target.dispose()
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database}"'))
        engine.dispose()


@pytest.mark.parametrize("legacy", ["fresh", "a2_flags", "a2_arrangements", "b3_flags", "b3_combined"])
def test_fresh_and_legacy_stamps_upgrade_without_losing_data(migration_database, legacy):
    config, engine = migration_database
    if legacy == "fresh":
        command.upgrade(config, "head")
    else:
        command.upgrade(config, "z1a2b3c4d5e6")
        with engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            if legacy in {"a2_arrangements", "b3_combined"}:
                ensure_session_arrangements(operations)
            if legacy in {"a2_flags", "b3_flags", "b3_combined"}:
                ensure_feature_flags(operations)
                connection.execute(text("INSERT INTO feature_flag_configs (environment, version, subscriptions_purchases_enabled, subscriptions_enforcement_enabled, launch_locked, updated_at) VALUES ('migration-test', 7, false, false, false, now())"))
            if legacy in {"b3_flags", "b3_combined"}:
                ensure_flag_targeting(operations)
            connection.execute(text("INSERT INTO users (id, email, preferred_name, password_hash, has_completed_onboarding, subscription_tier, coach_insights_enabled) VALUES ('migration-sentinel', 'migration@example.test', 'Preserve', 'test', false, 'free', false)"))
        command.stamp(config, "b3c4d5e6f7g8" if legacy.startswith("b3") else "a2b3c4d5e6f7")
        command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        schema = inspect(connection)
        assert {"effective_workout", "arrangement_revision"} <= {column["name"] for column in schema.get_columns("workout_sessions")}
        assert "payload_fingerprint" in {column["name"] for column in schema.get_columns("workout_session_operations")}
        assert {"feature_flag_configs", "feature_flag_audits", "feature_flag_user_overrides", "subscription_states", "subscription_transition_grants", "billing_webhook_events", "billing_jobs"} <= set(schema.get_table_names())
        assert {"subscriptions_purchases_audience", "subscriptions_enforcement_audience"} <= {column["name"] for column in schema.get_columns("feature_flag_configs")}
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "i0j1k2l3m4n5"
        if legacy != "fresh":
            assert connection.scalar(text("SELECT preferred_name FROM users WHERE id = 'migration-sentinel'")) == "Preserve"
        if legacy in {"a2_flags", "b3_flags", "b3_combined"}:
            assert connection.scalar(text("SELECT version FROM feature_flag_configs WHERE environment = 'migration-test'")) == 7
            assert connection.scalar(text("SELECT subscriptions_purchases_enabled FROM feature_flag_configs WHERE environment = 'migration-test'")) is False
        operations = Operations(MigrationContext.configure(connection))
        ensure_session_arrangements(operations)
        ensure_feature_flags(operations)
        ensure_flag_targeting(operations)


def _billing_race_database(migration_database, monkeypatch):
    from app.core import subscription_service as service
    from app.core.settings import settings
    from app.models.subscription import BillingJob, SubscriptionState
    from app.models.user import User

    config, engine = migration_database
    command.upgrade(config, "head")
    now = datetime.now(timezone.utc)
    user_id = "00000000-0000-4000-8000-000000000051"
    monkeypatch.setattr(settings, "APP_ENV", "test")
    monkeypatch.setattr(settings, "REVENUECAT_SECRET_API_KEY", "fixture-key")
    monkeypatch.setattr(settings, "REVENUECAT_PRODUCT_IDS", frozenset({"fixture.monthly"}))
    monkeypatch.setattr(service, "_now", lambda: now)
    with Session(engine) as db:
        db.add(User(id=user_id, email="race@example.test", preferred_name="Race", password_hash="test"))
        db.flush()
        db.add(SubscriptionState(environment="test", user_id=user_id, billing_status="unknown", verification_status="unverified", next_check_at=now - timedelta(minutes=1)))
        db.add(BillingJob(environment="test", user_id=user_id, kind="reconcile", status="pending", attempts=0, next_attempt_at=now, requested_at=now))
        db.commit()
    return engine, user_id, service


def test_sync_failure_and_worker_use_consistent_lock_order(migration_database, monkeypatch):
    from app.core import revenuecat_client

    engine, user_id, service = _billing_race_database(migration_database, monkeypatch)
    user_locked, worker_entered = threading.Event(), threading.Event()
    original = service.reconcile_subscription

    def fetch(_):
        if threading.current_thread().name.startswith("billing-sync"):
            user_locked.set()
            assert worker_entered.wait(5)
            raise revenuecat_client.RevenueCatUnavailable("fixture-outage")
        return {"subscriber": {"entitlements": {}}}

    def reconcile(db, user_id, **kwargs):
        if threading.current_thread().name.startswith("billing-worker"):
            worker_entered.set()
        return original(db, user_id, **kwargs)

    monkeypatch.setattr(revenuecat_client, "fetch_customer", fetch)
    monkeypatch.setattr(service, "reconcile_subscription", reconcile)

    def sync():
        with Session(engine) as db:
            with pytest.raises(service.SubscriptionAccessError):
                original(db, user_id)
            db.commit()

    def worker():
        with Session(engine) as db:
            assert service.process_billing_job(db)
            db.commit()

    with ThreadPoolExecutor(1, thread_name_prefix="billing-sync") as sync_pool, ThreadPoolExecutor(1, thread_name_prefix="billing-worker") as worker_pool:
        sync_result = sync_pool.submit(sync)
        assert user_locked.wait(5)
        worker_result = worker_pool.submit(worker)
        sync_result.result(timeout=10)
        worker_result.result(timeout=10)


def test_due_scheduler_does_not_hold_state_while_waiting_for_job(migration_database, monkeypatch):
    from app.core import revenuecat_client

    engine, user_id, service = _billing_race_database(migration_database, monkeypatch)
    job_locked, scheduler_entered = threading.Event(), threading.Event()
    original_reconcile, original_enqueue = service.reconcile_subscription, service.enqueue_reconciliation

    def reconcile(db, user_id, **kwargs):
        job_locked.set()
        assert scheduler_entered.wait(5)
        return original_reconcile(db, user_id, **kwargs)

    def enqueue(db, user_id, **kwargs):
        scheduler_entered.set()
        return original_enqueue(db, user_id, **kwargs)

    monkeypatch.setattr(service, "reconcile_subscription", reconcile)
    monkeypatch.setattr(service, "enqueue_reconciliation", enqueue)
    monkeypatch.setattr(revenuecat_client, "fetch_customer", lambda _: {"subscriber": {"entitlements": {}}})

    def worker():
        with Session(engine) as db:
            assert service.process_billing_job(db)
            db.commit()

    def schedule():
        with Session(engine) as db:
            assert service.schedule_due_reconciliations(db) == 1
            db.commit()

    with ThreadPoolExecutor(1) as worker_pool, ThreadPoolExecutor(1) as scheduler_pool:
        worker_result = worker_pool.submit(worker)
        assert job_locked.wait(5)
        scheduler_result = scheduler_pool.submit(schedule)
        worker_result.result(timeout=10)
        scheduler_result.result(timeout=10)
