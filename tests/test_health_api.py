import io
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.health_service import _cutoff, _local_today, purge_expired_days
from app.core.settings import settings
from app.main import app
from app.models.health import HealthDay, HealthPreference, HealthSource
from app.models.user import User
from conftest import LEGAL_ACCEPTANCE


client = TestClient(app)


@pytest.fixture
def accounts(monkeypatch):
    monkeypatch.setattr(settings, "HEALTH_COLLECTION_ENABLED", True)
    result = []
    for _ in range(2):
        response = client.post("/v1/auth/signup", json={
            "email": f"health-{uuid.uuid4().hex}@example.com",
            "password": "StrongPass123",
            "preferred_name": "Health Test",
            "legalAcceptance": LEGAL_ACCEPTANCE,
        })
        assert response.status_code == 201, response.text
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        user_id = client.get("/v1/users/me", headers=headers).json()["id"]
        result.append((user_id, headers))
    yield result
    with SessionLocal() as db:
        for user_id, _ in result:
            db.query(User).filter_by(id=user_id).delete(synchronize_session=False)
        db.commit()


def _connect(headers, source="apple_health", read=True, export=False):
    response = client.post("/v1/users/me/health/sources", headers=headers, json={
        "source": source, "readEnabled": read, "exportEnabled": export,
    })
    assert response.status_code == 200, response.text
    return response.json()


def _days(headers, revision, source="apple_health", days=None):
    return client.put("/v1/users/me/health/days", headers=headers, json={
        "source": source,
        "connectionRevision": revision,
        "days": days or [{"localDate": date.today().isoformat(), "timeZone": "America/New_York", "steps": 8234, "asleepMinutes": None}],
    })


def test_collection_is_disabled_by_default_and_requires_auth(accounts, monkeypatch):
    _, headers = accounts[0]
    assert client.get("/v1/users/me/health").status_code == 401
    monkeypatch.setattr(settings, "HEALTH_COLLECTION_ENABLED", False)
    response = client.get("/v1/users/me/health", headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["collectionAvailable"] is False
    assert client.post("/v1/users/me/health/sources", headers=headers, json={"source": "apple_health", "readEnabled": True}).status_code == 503


def test_local_date_boundary_uses_supplied_timezone():
    now = datetime(2026, 9, 26, 1, tzinfo=timezone.utc)
    assert _local_today(now, "America/Los_Angeles") == date(2026, 9, 25)
    assert _local_today(now, "Pacific/Auckland") == date(2026, 9, 26)
    assert _cutoff(now, "America/Los_Angeles") == date(2026, 9, 25) - timedelta(days=90)


def test_connection_revisions_upserts_isolation_and_disconnect(accounts):
    (user_id, headers), (_, other_headers) = accounts
    first = _connect(headers)
    revision = first["connectionRevision"]
    assert _connect(headers)["connectionRevision"] == revision
    assert _days(headers, revision).status_code == 200
    assert _days(headers, revision).status_code == 200
    with SessionLocal() as db:
        assert db.query(HealthDay).filter_by(user_id=user_id).count() == 1
    own = client.get("/v1/users/me/health", headers=headers).json()
    assert own["days"][0]["steps"] == 8234
    assert own["days"][0]["asleepMinutes"] is None
    assert client.get("/v1/users/me/health", headers=other_headers).json()["days"] == []
    assert _days(other_headers, revision).status_code == 409
    assert client.delete("/v1/users/me/health/sources/apple_health", headers=headers).status_code == 204
    assert _days(headers, revision).status_code == 409
    assert client.get("/v1/users/me/health", headers=headers).json()["days"] == []
    second = _connect(headers)
    assert second["connectionRevision"] > revision
    assert _days(headers, revision).status_code == 409


def test_primary_source_and_coach_preferences(accounts):
    _, headers = accounts[0]
    assert client.put("/v1/users/me/health/preferences", headers=headers, json={"primarySource": "apple_health", "coachEnabled": True}).status_code == 422
    _connect(headers, read=False, export=True)
    assert client.put("/v1/users/me/health/preferences", headers=headers, json={"primarySource": "apple_health", "coachEnabled": True}).status_code == 422
    _connect(headers, read=True, export=True)
    response = client.put("/v1/users/me/health/preferences", headers=headers, json={"primarySource": "apple_health", "coachEnabled": True})
    assert response.status_code == 200, response.text
    assert response.json()["coachEnabled"] is True
    _connect(headers, source="health_connect", read=True)
    response = client.put("/v1/users/me/health/preferences", headers=headers, json={"primarySource": "health_connect", "coachEnabled": True})
    assert response.status_code == 200
    assert response.json()["primarySource"] == "health_connect"
    client.delete("/v1/users/me/health/sources/health_connect", headers=headers)
    state = client.get("/v1/users/me/health", headers=headers).json()
    assert state["primarySource"] is None
    assert state["coachEnabled"] is False


def test_replacement_window_rebuilds_timezone_changed_days(accounts):
    user_id, headers = accounts[0]
    revision = _connect(headers)["connectionRevision"]
    today = date.today().isoformat()
    assert _days(headers, revision, days=[{"localDate": today, "timeZone": "America/New_York", "steps": 1200}]).status_code == 200
    response = client.put("/v1/users/me/health/days", headers=headers, json={
        "source": "apple_health", "connectionRevision": revision,
        "replaceWindow": {"startDate": today, "endDate": today, "timeZone": "America/Los_Angeles"},
        "days": [{"localDate": today, "timeZone": "America/Los_Angeles", "steps": None, "asleepMinutes": 420}],
    })
    assert response.status_code == 200, response.text
    own = client.get("/v1/users/me/health", headers=headers).json()["days"]
    assert len(own) == 1
    assert own[0]["timeZone"] == "America/Los_Angeles"
    assert own[0]["steps"] is None
    assert own[0]["asleepMinutes"] == 420
    with SessionLocal() as db:
        assert db.query(HealthDay).filter_by(user_id=user_id).count() == 1
    assert _days(headers, revision, days=[{"localDate": today, "timeZone": "America/New_York", "steps": 9000}]).status_code == 200
    own = client.get("/v1/users/me/health", headers=headers).json()["days"]
    assert len(own) == 1
    assert own[0]["timeZone"] == "America/New_York"


def test_daily_validation_retention_export_and_deletion(accounts):
    (user_id, headers), (_, other_headers) = accounts
    revision = _connect(headers)["connectionRevision"]
    assert _days(headers, revision, days=[{"localDate": date.today().isoformat(), "timeZone": "Invalid/Zone", "steps": 5}]).status_code == 422
    assert _days(headers, revision, days=[{"localDate": date.today().isoformat(), "timeZone": "UTC", "steps": 2147483648}]).status_code == 422
    assert _days(headers, revision, days=[{"localDate": (date.today() - timedelta(days=91)).isoformat(), "timeZone": "UTC", "steps": 5}]).status_code == 422
    response = _days(headers, revision)
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        db.add(HealthDay(user_id=user_id, source="apple_health", local_date=date.today() - timedelta(days=100), time_zone="UTC", steps=100, asleep_minutes=440))
        db.commit()
    state = client.get("/v1/users/me/health", headers=headers).json()
    assert len(state["days"]) == 1
    archive_response = client.get("/v1/users/me/export", headers=headers)
    assert archive_response.status_code == 200
    with ZipFile(io.BytesIO(archive_response.content)) as archive:
        data = json.loads(archive.read("account.json"))
        assert len(data["health_days"]) == 1
        assert data["health_days"][0]["steps"] == 8234
        assert "8234" in archive.read("health-days.csv").decode()
    other_archive = client.get("/v1/users/me/export", headers=other_headers)
    with ZipFile(io.BytesIO(other_archive.content)) as archive:
        data = json.loads(archive.read("account.json"))
        assert data["health_days"] == []
    assert client.delete("/v1/users/me", headers=headers).status_code == 204
    with SessionLocal() as db:
        assert db.query(HealthDay).filter_by(user_id=user_id).count() == 0
        assert db.query(HealthSource).filter_by(user_id=user_id).count() == 0
        assert db.query(HealthPreference).filter_by(user_id=user_id).count() == 0


def test_retention_job_removes_inactive_users_expired_data(accounts):
    user_id, headers = accounts[0]
    _connect(headers)
    with SessionLocal() as db:
        db.add(HealthDay(user_id=user_id, source="apple_health", local_date=date.today() - timedelta(days=100), time_zone="UTC", steps=1200))
        db.commit()
        assert purge_expired_days(db, datetime.now(timezone.utc)) >= 1
        assert db.query(HealthDay).filter_by(user_id=user_id).count() == 0
