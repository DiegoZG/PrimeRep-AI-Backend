import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session

from app.api.v1.feature_flags import router
from app.core import feature_flag_service as service
from app.core.database import get_db
from app.core.security.deps import get_current_user
from app.core.settings import settings
from app.models.feature_flags import FeatureFlagAudit, FeatureFlagConfig
from app.schemas.feature_flags import FeatureFlagPatch

ADMIN = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
PURCHASES = "subscriptions_purchases_enabled"
ENFORCEMENT = "subscriptions_enforcement_enabled"


@pytest.fixture
def flag_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated-flags.db'}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY)"))
        conn.execute(text("INSERT INTO users (id) VALUES (:admin), (:other)"), {"admin": ADMIN, "other": OTHER})
    FeatureFlagConfig.__table__.create(engine)
    FeatureFlagAudit.__table__.create(engine)
    monkeypatch.setattr(settings, "APP_ENV", "test")
    monkeypatch.setattr(settings, "FEATURE_FLAG_ADMIN_USER_IDS", frozenset({ADMIN}))
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.fixture
def client(flag_db):
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: flag_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=ADMIN)
    with TestClient(app) as client:
        yield client


def save(db, version=0, flags=None, **kwargs):
    return service.update_feature_flags(db, ADMIN, FeatureFlagPatch(expected_version=version, flags=flags or {}, **kwargs))


def test_defaults_are_read_only_and_public_hides_audit(client, flag_db):
    response = client.get("/v1/feature-flags")
    assert response.status_code == 200
    assert response.json() == {
        "environment": "test", "version": 0,
        "flags": {PURCHASES: False, ENFORCEMENT: False},
        "subscriptions_launch_at": None, "transition_ends_at": None,
        "launch_locked": False, "can_manage_flags": True, "updated_at": None,
    }
    assert client.get("/v1/admin/feature-flags").json()["history"] == []
    assert flag_db.scalar(select(func.count()).select_from(FeatureFlagConfig)) == 0
    save(flag_db, flags={PURCHASES: True})
    assert "history" not in client.get("/v1/feature-flags").json()


def test_authentication_and_admin_spoofing_denied(client):
    client.app.dependency_overrides.pop(get_current_user)
    assert client.get("/v1/feature-flags").status_code == 401
    client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=OTHER)
    assert client.get("/v1/feature-flags").json()["can_manage_flags"] is False
    headers = {"X-User-Id": ADMIN, "X-Admin": "true", "X-Environment": "production"}
    assert client.get("/v1/admin/feature-flags", headers=headers).status_code == 403
    response = client.patch("/v1/admin/feature-flags", headers=headers, json={"expected_version": 0, "flags": {PURCHASES: True}})
    assert response.status_code == 403
    assert isinstance(response.json()["detail"], str)


def test_empty_allowlist_denies_all(client, monkeypatch):
    monkeypatch.setattr(settings, "FEATURE_FLAG_ADMIN_USER_IDS", frozenset())
    assert client.get("/v1/feature-flags").json()["can_manage_flags"] is False
    assert client.get("/v1/admin/feature-flags").status_code == 403
    assert client.patch("/v1/admin/feature-flags", json={"expected_version": 0}).status_code == 403


@pytest.mark.parametrize("body", [
    {"expected_version": 0, "environment": "production"},
    {"expected_version": 0, "can_manage_flags": True},
    {"expected_version": 0, "flags": {"unknown": True}},
    {"expected_version": 0, "flags": {PURCHASES: "false"}},
    {"expected_version": 0, "flags": {PURCHASES: 1}},
    {"expected_version": 0, "flags": {PURCHASES: None}},
    {"expected_version": True}, {"expected_version": "0"},
    {"expected_version": -1}, {"expected_version": 1.5},
    {"expected_version": 0, "subscriptions_launch_at": "2026-10-01T12:00:00"},
    {"expected_version": 0, "subscriptions_launch_at": "2026-10-01"},
    {"expected_version": 0, "subscriptions_launch_at": 1234567890},
    {"expected_version": 0, "subscriptions_launch_at": "1234567890"},
    {"expected_version": 0, "flags": None},
])
def test_strict_patch_validation(client, body):
    response = client.patch("/v1/admin/feature-flags", json=body)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_no_op_preserves_version_and_history(flag_db):
    assert save(flag_db)["version"] == 0
    assert flag_db.get(FeatureFlagConfig, "test") is None
    saved = save(flag_db, flags={PURCHASES: True})
    repeated = save(flag_db, 1, {PURCHASES: True})
    assert repeated == saved
    with pytest.raises(HTTPException) as error:
        save(flag_db, 0, {PURCHASES: True})
    assert error.value.status_code == 409


def test_launch_requirements_and_freeze_survive_rollback(flag_db):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    for flags, date in [({ENFORCEMENT: True}, past), ({PURCHASES: True, ENFORCEMENT: True}, future), ({PURCHASES: True, ENFORCEMENT: True}, None)]:
        with pytest.raises(HTTPException) as error:
            save(flag_db, flags=flags, subscriptions_launch_at=date)
        assert error.value.status_code == 422
    saved = save(flag_db, flags={PURCHASES: True, ENFORCEMENT: True}, subscriptions_launch_at=past)
    assert saved["launch_locked"] is True
    assert saved["transition_ends_at"] == past + timedelta(days=30)
    for date in [None, future, past - timedelta(seconds=1)]:
        with pytest.raises(HTTPException) as error:
            save(flag_db, 1, {ENFORCEMENT: False}, subscriptions_launch_at=date)
        assert error.value.status_code == 422
    with pytest.raises(HTTPException):
        save(flag_db, 1, {PURCHASES: False})
    disabled = save(flag_db, 1, {PURCHASES: False, ENFORCEMENT: False})
    assert disabled["launch_locked"] is True
    assert disabled["subscriptions_launch_at"] == past
    with pytest.raises(HTTPException):
        save(flag_db, 2, subscriptions_launch_at=None)
    enabled = save(flag_db, 2, {PURCHASES: True, ENFORCEMENT: True})
    assert enabled["transition_ends_at"] == saved["transition_ends_at"]


def test_offsets_normalize_and_date_can_clear_before_lock(client):
    body = {"expected_version": 0, "subscriptions_launch_at": "2026-10-01T12:00:00-04:00"}
    saved = client.patch("/v1/admin/feature-flags", json=body).json()
    assert saved["subscriptions_launch_at"] == "2026-10-01T16:00:00Z"
    assert saved["transition_ends_at"] == "2026-10-31T16:00:00Z"
    cleared = client.patch("/v1/admin/feature-flags", json={"expected_version": 1, "subscriptions_launch_at": None}).json()
    assert cleared["subscriptions_launch_at"] is None
    assert cleared["version"] == 2


def test_environment_isolation_normalization_and_no_process_cache(flag_db, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "prod")
    saved = save(flag_db, flags={PURCHASES: True})
    assert saved["environment"] == "production"
    monkeypatch.setattr(settings, "APP_ENV", "PRODUCTION")
    assert service.get_feature_flags(flag_db, ADMIN)["version"] == 1
    monkeypatch.setattr(settings, "APP_ENV", "preview")
    assert service.get_feature_flags(flag_db, ADMIN, admin=True)["history"] == []
    assert service.get_feature_flags(flag_db, ADMIN)["version"] == 0
    monkeypatch.setattr(settings, "APP_ENV", "production")
    with Session(flag_db.bind) as other:
        save(other, 1, {PURCHASES: False})
    assert service.get_feature_flags(flag_db, ADMIN)["flags"][PURCHASES] is False


def test_audit_has_actor_before_after_and_newest_twenty(flag_db):
    for version in range(23):
        save(flag_db, version, {PURCHASES: version % 2 == 0})
    history = service.get_feature_flags(flag_db, ADMIN, admin=True)["history"]
    assert [row["version"] for row in history] == list(range(23, 3, -1))
    assert all(row["changed_by"] == ADMIN for row in history)
    assert history[0]["before"]["flags"][PURCHASES] is False
    assert history[0]["after"]["flags"][PURCHASES] is True
    flag_db.execute(text("DELETE FROM users WHERE id = :id"), {"id": ADMIN})
    flag_db.commit()
    assert service.get_feature_flags(flag_db, ADMIN, admin=True)["history"][0]["changed_by"] is None


def test_failed_audit_flush_rolls_back_configuration(flag_db, monkeypatch):
    original_flush = flag_db.flush

    def fail_audit(*args, **kwargs):
        if any(isinstance(row, FeatureFlagAudit) for row in flag_db.new):
            raise RuntimeError("simulated audit failure")
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(flag_db, "flush", fail_audit)
    with pytest.raises(RuntimeError, match="simulated audit failure"):
        save(flag_db, flags={PURCHASES: True})
    assert flag_db.get(FeatureFlagConfig, "test") is None
    assert flag_db.scalar(select(func.count()).select_from(FeatureFlagAudit)) == 0


def test_admin_snapshot_history_excludes_later_concurrent_save(flag_db, monkeypatch):
    save(flag_db, flags={PURCHASES: True})
    original_history = service._history

    def race_history(db, environment, version):
        with monkeypatch.context() as scoped:
            scoped.setattr(service, "_history", original_history)
            with Session(flag_db.bind) as other:
                save(other, 1, {PURCHASES: False})
        return original_history(db, environment, version)

    monkeypatch.setattr(service, "_history", race_history)
    result = service.get_feature_flags(flag_db, ADMIN, admin=True)
    assert result["version"] == 1
    assert [item["version"] for item in result["history"]] == [1]


def test_simultaneous_first_insert_returns_conflict(flag_db, monkeypatch):
    original_scalar = flag_db.scalar
    raced = False

    def competing_insert(*args, **kwargs):
        nonlocal raced
        result = original_scalar(*args, **kwargs)
        if not raced:
            raced = True
            assert result is None
            with Session(flag_db.bind) as other:
                save(other, flags={PURCHASES: True})
        return result

    monkeypatch.setattr(flag_db, "scalar", competing_insert)
    with pytest.raises(HTTPException) as error:
        save(flag_db, flags={PURCHASES: True})
    assert error.value.status_code == 409
    result = service.get_feature_flags(flag_db, ADMIN, admin=True)
    assert result["version"] == 1
    assert len(result["history"]) == 1


def test_existing_row_cas_rejects_racing_writer(flag_db, monkeypatch):
    save(flag_db, flags={PURCHASES: True})
    original_execute = flag_db.execute
    raced = False

    def competing_update(statement, *args, **kwargs):
        nonlocal raced
        if getattr(statement, "is_update", False) and not raced:
            raced = True
            with Session(flag_db.bind) as other:
                save(other, 1, {PURCHASES: False})
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(flag_db, "execute", competing_update)
    with pytest.raises(HTTPException) as error:
        save(flag_db, 1, subscriptions_launch_at=datetime.now(timezone.utc))
    assert error.value.status_code == 409
    result = service.get_feature_flags(flag_db, ADMIN, admin=True)
    assert result["version"] == 2
    assert result["subscriptions_launch_at"] is None
    assert len(result["history"]) == 2


def test_migration_roundtrip_on_isolated_database(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/a2b3c4d5e6f7_add_subscription_feature_flags.py"
    spec = importlib.util.spec_from_file_location("feature_flag_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated-migration.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY)"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.upgrade()
        assert conn.execute(text("SELECT COUNT(*) FROM feature_flag_configs")).scalar() == 0
        migration.downgrade()
        migration.upgrade()
        assert conn.execute(text("SELECT COUNT(*) FROM feature_flag_audits")).scalar() == 0
    engine.dispose()
