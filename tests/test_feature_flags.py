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
from app.models.feature_flags import FeatureFlagAudit, FeatureFlagConfig, FeatureFlagUserOverride
from app.schemas.feature_flags import FeatureFlagPatch, UserFeatureFlagPatch

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
        conn.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT, preferred_name TEXT)"))
        conn.execute(text("INSERT INTO users (id, email, preferred_name) VALUES (:admin, 'admin@example.test', 'Admin'), (:other, 'other@example.test', 'Other')"), {"admin": ADMIN, "other": OTHER})
    FeatureFlagConfig.__table__.create(engine)
    FeatureFlagAudit.__table__.create(engine)
    FeatureFlagUserOverride.__table__.create(engine)
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


def save_user(db, user_id=OTHER, version=0, **overrides):
    return service.update_user_feature_flags(db, ADMIN, user_id, UserFeatureFlagPatch(
        expected_version=version, overrides=overrides,
    ))


def test_selected_audience_is_per_user_and_public_does_not_leak_rules(client, flag_db):
    saved = save(flag_db, flags={PURCHASES: True}, audiences={PURCHASES: "selected"})
    assert saved["audiences"] == {PURCHASES: "selected", ENFORCEMENT: "everyone"}
    assert client.get("/v1/feature-flags").json()["flags"][PURCHASES] is False
    selected = save_user(flag_db, version=1, **{PURCHASES: True})
    assert selected["effective_flags"][PURCHASES] is True
    assert service.get_feature_flags(flag_db, ADMIN)["flags"][PURCHASES] is False
    client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=OTHER)
    public = client.get("/v1/feature-flags").json()
    assert public["flags"][PURCHASES] is True
    assert not {"user", "overrides", "audiences", "history", "target_user_id", "global_flags"}.intersection(public)
    assert "admin@example.test" not in str(public)


def test_master_off_wins_and_staged_allow_can_be_saved(flag_db):
    staged = save_user(flag_db, **{PURCHASES: True, ENFORCEMENT: True})
    assert staged["global_flags"] == {PURCHASES: False, ENFORCEMENT: False}
    assert staged["overrides"] == {PURCHASES: True, ENFORCEMENT: True}
    assert staged["effective_flags"] == {PURCHASES: False, ENFORCEMENT: False}
    save(flag_db, 1, {PURCHASES: True}, audiences={PURCHASES: "selected"})
    assert service.get_user_feature_flags(flag_db, ADMIN, OTHER)["effective_flags"][PURCHASES] is True
    save(flag_db, 2, {PURCHASES: False})
    assert service.get_user_feature_flags(flag_db, ADMIN, OTHER)["effective_flags"][PURCHASES] is False


def test_allow_deny_inherit_and_remove_override_row(flag_db):
    save(flag_db, flags={PURCHASES: True})
    denied = save_user(flag_db, version=1, **{PURCHASES: False})
    assert denied["effective_flags"][PURCHASES] is False
    allowed = save_user(flag_db, version=2, **{PURCHASES: True})
    assert allowed["effective_flags"][PURCHASES] is True
    inherited = save_user(flag_db, version=3, **{PURCHASES: None})
    assert inherited["effective_flags"][PURCHASES] is True
    assert inherited["overrides"] == {PURCHASES: None, ENFORCEMENT: None}
    assert flag_db.get(FeatureFlagUserOverride, ("test", OTHER)) is None
    assert save_user(flag_db, version=4, **{PURCHASES: None})["version"] == 4
    assert len(service.get_feature_flags(flag_db, ADMIN, admin=True)["history"]) == 4


def test_individual_null_clears_only_requested_rule(flag_db):
    save_user(flag_db, **{PURCHASES: True, ENFORCEMENT: False})
    changed = save_user(flag_db, version=1, **{PURCHASES: None})
    assert changed["overrides"] == {PURCHASES: None, ENFORCEMENT: False}
    assert flag_db.get(FeatureFlagUserOverride, ("test", OTHER)) is not None


def test_enforcement_requires_effective_purchases_and_clock_never_restarts(flag_db):
    launch = datetime.now(timezone.utc) - timedelta(days=4)
    save(flag_db, flags={PURCHASES: True, ENFORCEMENT: True}, subscriptions_launch_at=launch)
    denied = save_user(flag_db, version=1, **{PURCHASES: False, ENFORCEMENT: True})
    assert denied["effective_flags"] == {PURCHASES: False, ENFORCEMENT: False}
    save(flag_db, 2, audiences={PURCHASES: "selected", ENFORCEMENT: "selected"})
    enabled = save_user(flag_db, version=3, **{PURCHASES: True})
    assert enabled["effective_flags"] == {PURCHASES: True, ENFORCEMENT: True}
    assert enabled["subscriptions_launch_at"] == launch
    assert enabled["transition_ends_at"] == launch + timedelta(days=30)
    assert enabled["launch_locked"] is True
    save(flag_db, 4, audiences={PURCHASES: "everyone", ENFORCEMENT: "everyone"})
    cleared = save_user(flag_db, version=5, **{PURCHASES: None, ENFORCEMENT: None})
    assert cleared["transition_ends_at"] == enabled["transition_ends_at"]


@pytest.mark.parametrize("body", [
    {"expected_version": 0, "audiences": {PURCHASES: "all"}},
    {"expected_version": 0, "audiences": {PURCHASES: None}},
    {"expected_version": 0, "audiences": {"unknown": "selected"}},
    {"expected_version": 0, "audiences": None},
])
def test_audience_validation(client, body):
    response = client.patch("/v1/admin/feature-flags", json=body)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize("body", [
    {"expected_version": 0, "overrides": {PURCHASES: "true"}},
    {"expected_version": 0, "overrides": {PURCHASES: 1}},
    {"expected_version": 0, "overrides": {"unknown": True}},
    {"expected_version": 0, "overrides": None},
    {"expected_version": True, "overrides": {}},
    {"expected_version": 0, "overrides": {}, "environment": "production"},
    {"expected_version": 0},
])
def test_override_validation(client, body):
    response = client.patch(f"/v1/admin/feature-flags/users/{OTHER}", json=body)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_lookup_exact_email_or_uuid_and_read_only_defaults(client, flag_db):
    for identifier in (OTHER, "other@example.test", " OTHER@EXAMPLE.TEST "):
        response = client.post("/v1/admin/feature-flags/users/lookup", json={"identifier": identifier})
        assert response.status_code == 200
        result = response.json()
        assert result["user"] == {"id": OTHER, "email": "other@example.test", "preferred_name": "Other"}
        assert result["version"] == 0
        assert result["overrides"] == {PURCHASES: None, ENFORCEMENT: None}
    for identifier in ("other", "other@", "example.test", "00000000-0000-4000-8000-000000000099"):
        assert client.post("/v1/admin/feature-flags/users/lookup", json={"identifier": identifier}).status_code == 404
    assert flag_db.get(FeatureFlagConfig, "test") is None
    assert flag_db.scalar(select(func.count()).select_from(FeatureFlagAudit)) == 0
    assert client.get(f"/v1/admin/feature-flags/users/{OTHER}").status_code == 200
    assert client.post("/v1/admin/feature-flags/users/lookup", json={"identifier": OTHER, "admin": True}).status_code == 422


def test_unknown_target_has_no_side_effects(client, flag_db):
    unknown = "00000000-0000-4000-8000-000000000099"
    assert client.get(f"/v1/admin/feature-flags/users/{unknown}").status_code == 404
    assert client.patch(f"/v1/admin/feature-flags/users/{unknown}", json={"expected_version": 0, "overrides": {PURCHASES: True}}).status_code == 404
    assert flag_db.get(FeatureFlagConfig, "test") is None


def test_normal_users_cannot_read_or_write_targeting_and_lookup_checks_auth_first(client, flag_db, monkeypatch):
    client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=OTHER)

    def unexpected_lookup(*args, **kwargs):
        raise AssertionError("unauthorized request reached target lookup")

    monkeypatch.setattr(service, "_target", unexpected_lookup)
    monkeypatch.setattr(flag_db, "scalar", unexpected_lookup)
    for url in ("/v1/admin/feature-flags/users", f"/v1/admin/feature-flags/users/{ADMIN}"):
        assert client.get(url).status_code == 403
    assert client.post("/v1/admin/feature-flags/users/lookup", json={"identifier": "admin@example.test"}).status_code == 403
    assert client.patch(f"/v1/admin/feature-flags/users/{ADMIN}", json={"expected_version": 0, "overrides": {PURCHASES: True}}).status_code == 403


def test_public_cannot_select_another_user_or_environment(client):
    for selector in (f"user_id={OTHER}", "environment=production", f"identifier={OTHER}"):
        assert client.get(f"/v1/feature-flags?{selector}").status_code == 422


def test_list_only_override_users_is_paginated_and_sorted(client, flag_db):
    assert client.get("/v1/admin/feature-flags/users").json() == {
        "environment": "test", "version": 0, "items": [], "total": 0, "limit": 20, "offset": 0,
    }
    save_user(flag_db, OTHER, **{PURCHASES: True})
    save_user(flag_db, ADMIN, 1, **{ENFORCEMENT: False})
    result = client.get("/v1/admin/feature-flags/users?limit=1&offset=0").json()
    assert result["total"] == 2
    assert result["version"] == 2
    assert [item["user"]["id"] for item in result["items"]] == [ADMIN]
    assert client.get("/v1/admin/feature-flags/users?limit=1&offset=1").json()["items"][0]["user"]["id"] == OTHER
    assert client.get("/v1/admin/feature-flags/users?offset=99").json()["items"] == []
    save_user(flag_db, ADMIN, 2, **{ENFORCEMENT: None})
    assert client.get("/v1/admin/feature-flags/users").json()["total"] == 1
    for query in ("limit=0", "limit=101", "offset=-1"):
        assert client.get(f"/v1/admin/feature-flags/users?{query}").status_code == 422


def test_shared_versions_and_audits_for_global_and_user_changes(flag_db):
    save(flag_db, flags={PURCHASES: True})
    save_user(flag_db, version=1, **{PURCHASES: False})
    with pytest.raises(HTTPException) as error:
        save(flag_db, 1, {PURCHASES: False})
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        save_user(flag_db, version=1, **{PURCHASES: True})
    assert error.value.status_code == 409
    save(flag_db, 2, audiences={PURCHASES: "selected"})
    history = service.get_feature_flags(flag_db, ADMIN, admin=True)["history"]
    assert [item["version"] for item in history] == [3, 2, 1]
    assert [item["scope"] for item in history] == ["global", "user", "global"]
    assert history[1]["target_user_id"] == OTHER
    assert history[1]["after"] == {"overrides": {PURCHASES: False, ENFORCEMENT: None}}
    assert "email" not in str(history[1])
    assert "preferred_name" not in str(history[1])


def test_user_write_no_op_and_failed_audit_roll_back_all_state(flag_db, monkeypatch):
    assert save_user(flag_db)["version"] == 0
    assert flag_db.get(FeatureFlagConfig, "test") is None
    save(flag_db, flags={PURCHASES: True})
    original_flush = flag_db.flush

    def fail_audit(*args, **kwargs):
        if any(isinstance(row, FeatureFlagAudit) for row in flag_db.new):
            raise RuntimeError("simulated targeting audit failure")
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(flag_db, "flush", fail_audit)
    with pytest.raises(RuntimeError, match="simulated targeting audit failure"):
        save_user(flag_db, version=1, **{PURCHASES: False})
    assert flag_db.get(FeatureFlagConfig, "test").version == 1
    assert flag_db.get(FeatureFlagUserOverride, ("test", OTHER)) is None
    assert flag_db.scalar(select(func.count()).select_from(FeatureFlagAudit)) == 1


def test_user_first_insert_race_conflicts_without_orphan_override(flag_db, monkeypatch):
    original_scalar = flag_db.scalar
    raced = False

    def race(*args, **kwargs):
        nonlocal raced
        result = original_scalar(*args, **kwargs)
        if not raced:
            raced = True
            with Session(flag_db.bind) as other:
                save(other, flags={PURCHASES: True})
        return result

    monkeypatch.setattr(flag_db, "scalar", race)
    with pytest.raises(HTTPException) as error:
        save_user(flag_db, **{PURCHASES: True})
    assert error.value.status_code == 409
    assert flag_db.get(FeatureFlagUserOverride, ("test", OTHER)) is None
    assert flag_db.get(FeatureFlagConfig, "test").version == 1


def test_user_cas_race_with_global_write_has_no_lost_audit(flag_db, monkeypatch):
    save(flag_db, flags={PURCHASES: True})
    original_execute = flag_db.execute
    raced = False

    def race(statement, *args, **kwargs):
        nonlocal raced
        if getattr(statement, "is_update", False) and not raced:
            raced = True
            with Session(flag_db.bind) as other:
                save(other, 1, {PURCHASES: False})
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(flag_db, "execute", race)
    with pytest.raises(HTTPException) as error:
        save_user(flag_db, version=1, **{PURCHASES: True})
    assert error.value.status_code == 409
    assert flag_db.get(FeatureFlagUserOverride, ("test", OTHER)) is None
    assert len(service.get_feature_flags(flag_db, ADMIN, admin=True)["history"]) == 2


def test_deleted_user_rules_cascade_and_audit_target_is_anonymized(flag_db):
    save_user(flag_db, **{PURCHASES: True})
    flag_db.execute(text("DELETE FROM users WHERE id = :id"), {"id": OTHER})
    flag_db.commit()
    assert flag_db.get(FeatureFlagUserOverride, ("test", OTHER)) is None
    history = service.get_feature_flags(flag_db, ADMIN, admin=True)["history"]
    assert history[0]["target_user_id"] is None
    assert OTHER not in str(history)
    assert service.list_user_feature_flags(flag_db, ADMIN, 20, 0)["items"] == []


def test_targeting_is_environment_scoped(flag_db, monkeypatch):
    save(flag_db, flags={PURCHASES: True}, audiences={PURCHASES: "selected"})
    save_user(flag_db, version=1, **{PURCHASES: True})
    monkeypatch.setattr(settings, "APP_ENV", "preview")
    assert service.get_user_feature_flags(flag_db, ADMIN, OTHER)["overrides"][PURCHASES] is None
    assert service.list_user_feature_flags(flag_db, ADMIN, 20, 0)["items"] == []
    save(flag_db, flags={PURCHASES: True})
    save_user(flag_db, version=1, **{PURCHASES: False})
    monkeypatch.setattr(settings, "APP_ENV", "test")
    assert service.get_user_feature_flags(flag_db, ADMIN, OTHER)["effective_flags"][PURCHASES] is True


def test_targeting_migration_preserves_existing_config_and_history(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'targeting-migration.db'}")
    migrations = []
    for filename in ("a2b3c4d5e6f7_add_subscription_feature_flags.py", "b3c4d5e6f7g8_add_feature_flag_user_targeting.py"):
        path = Path(__file__).resolve().parents[1] / "alembic/versions" / filename
        spec = importlib.util.spec_from_file_location(filename[:-3], path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        migrations.append(module)
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY)"))
        operations = Operations(MigrationContext.configure(conn))
        for module in migrations:
            monkeypatch.setattr(module, "op", operations)
        migrations[0].upgrade()
        conn.execute(text("INSERT INTO feature_flag_configs (environment, version, subscriptions_purchases_enabled, updated_at) VALUES ('test', 7, true, CURRENT_TIMESTAMP)"))
        conn.execute(text("INSERT INTO feature_flag_audits (environment, version, changed_at, before, after) VALUES ('test', 7, CURRENT_TIMESTAMP, '{}', '{}')"))
        migrations[1].upgrade()
        assert conn.execute(text("SELECT subscriptions_purchases_audience, subscriptions_enforcement_audience FROM feature_flag_configs")).one() == ("everyone", "everyone")
        assert conn.execute(text("SELECT scope, target_user_id FROM feature_flag_audits")).one() == ("global", None)
        migrations[1].downgrade()
        assert conn.execute(text("SELECT version FROM feature_flag_configs")).scalar() == 7
        assert conn.execute(text("SELECT version FROM feature_flag_audits")).scalar() == 7
        migrations[1].upgrade()
        assert conn.execute(text("SELECT COUNT(*) FROM feature_flag_user_overrides")).scalar() == 0
    engine.dispose()
