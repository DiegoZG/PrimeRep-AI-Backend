import io
import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core import coach_feed_service, health_service
from app.core.health_service import _cutoff, _local_today, purge_expired_days
from app.core.settings import settings
from app.main import app
from app.models.health import HealthDay, HealthPreference, HealthSource
from app.models.coach import CoachFeedItem, CoachNotificationJob
from app.models.user import User
from app.schemas.health import HealthDaysRequest, HealthPreferencesRequest, HealthSourceRequest
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


def _coach_days(today, *, sleep_baseline, steps_baseline, observed_sleep, observed_steps, zone="UTC"):
    days = []
    for index in range(7):
        days.append({
            "localDate": (today - timedelta(days=8 - index)).isoformat(),
            "timeZone": zone,
            "steps": steps_baseline[index],
            "asleepMinutes": sleep_baseline[index],
        })
    days.append({
        "localDate": (today - timedelta(days=1)).isoformat(),
        "timeZone": zone,
        "steps": observed_steps,
        "asleepMinutes": observed_sleep,
    })
    return days


def _active_health_items(db, user_id):
    return db.query(CoachFeedItem).filter(
        CoachFeedItem.user_id == user_id,
        CoachFeedItem.kind == "health_context",
        CoachFeedItem.expires_at > coach_feed_service._now(),
        CoachFeedItem.invalidated_at.is_(None),
    ).all()


def test_health_coach_requires_four_valid_baseline_days_and_prefers_sleep(accounts, monkeypatch):
    user_id, headers = accounts[0]
    today = datetime.now(timezone.utc).date()
    now = datetime.combine(today, time(12), tzinfo=timezone.utc)
    monkeypatch.setattr(coach_feed_service, "_now", lambda: now)
    revision = _connect(headers)["connectionRevision"]
    days = _coach_days(
        today,
        sleep_baseline=[480, 465, None, None, None, None, None],
        steps_baseline=[6000, 6200, 6100, None, None, None, None],
        observed_sleep=290,
        observed_steps=16000,
    )
    assert _days(headers, revision, days=days).status_code == 200
    assert client.put("/v1/users/me/health/preferences", headers=headers, json={
        "primarySource": "apple_health", "coachEnabled": True,
    }).status_code == 200
    with SessionLocal() as db:
        assert coach_feed_service._health_context_candidates(db, user_id) == []

    days[3]["asleepMinutes"] = 450
    days[3]["steps"] = 6050
    days[4]["asleepMinutes"] = 475
    days[4]["steps"] = 6150
    assert _days(headers, revision, days=days).status_code == 200
    with SessionLocal() as db:
        coach_feed_service.reconcile_feed(db, user_id, today)
        items = _active_health_items(db, user_id)
        assert len(items) == 1
        item = items[0]
        assert item.title == "Check in before your next workout"
        assert "Apple Health" in item.detail
        assert "4 h 50 min" in item.detail
        assert item.target_data == {"type": "settings", "section": "health"}
        assert item.ai_status in {"not_eligible", "fallback"}
        assert coach_feed_service._item_out(item, ai_eligible=True).is_ai_assisted is False
        assert db.query(CoachNotificationJob).filter_by(feed_item_id=item.id).count() == 0
        assert _active_health_items(db, accounts[1][0]) == []
        assert coach_feed_service.is_item_current(db, item)
        coach_feed_service.reconcile_feed(db, user_id, today)
        assert len(_active_health_items(db, user_id)) == 1
        monkeypatch.setattr(coach_feed_service.settings, "ANTHROPIC_API_KEY", "test")
        monkeypatch.setattr(coach_feed_service, "_call_coach_feed_ai", lambda *args: pytest.fail("Health sent to AI"))
        assert coach_feed_service.enrich_pending_items(db, user_id) == 0
    days[-1]["asleepMinutes"] = 280
    assert _days(headers, revision, days=days).status_code == 200
    with SessionLocal() as db:
        coach_feed_service.reconcile_feed(db, user_id, today)
        assert db.query(CoachFeedItem).filter_by(user_id=user_id, kind="health_context").count() == 1
        assert len(_active_health_items(db, user_id)) == 1


def test_health_kill_switch_hides_persisted_coach_items(accounts, monkeypatch):
    user_id, headers = accounts[0]
    today = datetime.now(timezone.utc).date()
    revision = _connect(headers)["connectionRevision"]
    days = _coach_days(
        today,
        sleep_baseline=[470] * 7,
        steps_baseline=[6000] * 7,
        observed_sleep=300,
        observed_steps=None,
    )
    assert _days(headers, revision, days=days).status_code == 200
    assert client.put("/v1/users/me/health/preferences", headers=headers, json={
        "primarySource": "apple_health", "coachEnabled": True,
    }).status_code == 200
    with SessionLocal() as db:
        coach_feed_service.reconcile_feed(db, user_id, today)
        item = _active_health_items(db, user_id)[0]
        item_id = item.id
    params = {"localDate": (today + timedelta(days=1)).isoformat(), "view": "last7Days"}
    before = client.get("/v1/coach/feed", headers=headers, params=params)
    assert before.status_code == 200
    assert item_id in [item["id"] for item in before.json()["items"]]

    monkeypatch.setattr(settings, "HEALTH_COLLECTION_ENABLED", False)
    after = client.get("/v1/coach/feed", headers=headers, params=params)
    assert after.status_code == 200
    assert item_id not in [item["id"] for item in after.json()["items"]]
    with SessionLocal() as db:
        item = db.query(CoachFeedItem).filter_by(id=item_id).one()
        assert coach_feed_service.is_item_current(db, item) is False
    assert client.get(f"/v1/coach/items/{item_id}", headers=headers).status_code == 404
    assert client.post(f"/v1/coach/items/{item_id}/read", headers=headers, json={"reason": "expanded"}).status_code == 404


def test_health_coach_steps_source_switch_opt_out_and_disconnect(accounts, monkeypatch):
    user_id, headers = accounts[0]
    today = datetime.now(timezone.utc).date()
    now = datetime.combine(today, time(12), tzinfo=timezone.utc)
    monkeypatch.setattr(coach_feed_service, "_now", lambda: now)
    days = _coach_days(
        today,
        sleep_baseline=[480] * 7,
        steps_baseline=[6000] * 7,
        observed_sleep=450,
        observed_steps=14000,
    )
    apple_days = [dict(day) for day in days]
    apple_days[-1]["steps"] = 7000
    apple = _connect(headers, "apple_health")
    android = _connect(headers, "health_connect")
    assert _days(headers, apple["connectionRevision"], "apple_health", apple_days).status_code == 200
    assert _days(headers, android["connectionRevision"], "health_connect", days).status_code == 200
    for source in ("apple_health", "health_connect"):
        assert client.put("/v1/users/me/health/preferences", headers=headers, json={
            "primarySource": source, "coachEnabled": True,
        }).status_code == 200
        with SessionLocal() as db:
            assert _active_health_items(db, user_id) == []
            coach_feed_service.reconcile_feed(db, user_id, today)
            items = _active_health_items(db, user_id)
            assert len(items) == (0 if source == "apple_health" else 1)
            if items:
                assert "Health Connect" in items[0].detail
                assert "14,000 steps" in items[0].detail
    assert client.put("/v1/users/me/health/preferences", headers=headers, json={
        "primarySource": "health_connect", "coachEnabled": False,
    }).status_code == 200
    with SessionLocal() as db:
        assert _active_health_items(db, user_id) == []
        coach_feed_service.reconcile_feed(db, user_id, today)
        assert _active_health_items(db, user_id) == []
    assert client.put("/v1/users/me/health/preferences", headers=headers, json={
        "primarySource": "health_connect", "coachEnabled": True,
    }).status_code == 200
    with SessionLocal() as db:
        coach_feed_service.reconcile_feed(db, user_id, today)
        assert len(_active_health_items(db, user_id)) == 1
    _connect(headers, source="health_connect", read=False, export=True)
    with SessionLocal() as db:
        assert _active_health_items(db, user_id) == []
        assert db.get(HealthPreference, user_id).coach_enabled is False
    assert client.delete("/v1/users/me/health/sources/health_connect", headers=headers).status_code == 204
    with SessionLocal() as db:
        assert db.query(CoachFeedItem).filter_by(user_id=user_id, kind="health_context").count() == 0


def test_health_coach_uses_local_wake_date_across_dst(accounts, monkeypatch):
    user_id, headers = accounts[0]
    _connect(headers)
    assert client.put("/v1/users/me/health/preferences", headers=headers, json={
        "primarySource": "apple_health", "coachEnabled": True,
    }).status_code == 200
    today = date(2026, 11, 2)
    now = datetime(2026, 11, 2, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(coach_feed_service, "_now", lambda: now)
    days = _coach_days(
        today,
        sleep_baseline=[470] * 7,
        steps_baseline=[6000] * 7,
        observed_sleep=300,
        observed_steps=None,
        zone="America/New_York",
    )
    with SessionLocal() as db:
        for day in days:
            db.add(HealthDay(
                user_id=user_id,
                source="apple_health",
                local_date=date.fromisoformat(day["localDate"]),
                time_zone=day["timeZone"],
                steps=day["steps"],
                asleep_minutes=day["asleepMinutes"],
            ))
        db.query(HealthSource).filter_by(user_id=user_id, source="apple_health").one().last_successful_sync_at = now
        db.commit()
        candidate = coach_feed_service._health_context_candidates(db, user_id)[0]
        assert candidate["expires_at"] == datetime(2026, 11, 3, 5, tzinfo=timezone.utc)
        assert "2026-11-01" in candidate["detail"]
        source = db.query(HealthSource).filter_by(user_id=user_id, source="apple_health").one()
        source.last_successful_sync_at = datetime(2026, 11, 2, 4, 59, tzinfo=timezone.utc)
        db.flush()
        assert coach_feed_service._health_context_candidates(db, user_id) == []
        source.last_successful_sync_at = datetime(2026, 11, 2, 5, 1, tzinfo=timezone.utc)
        db.flush()
        assert len(coach_feed_service._health_context_candidates(db, user_id)) == 1


@pytest.mark.parametrize("operation", ["source", "preferences", "days", "disconnect"])
def test_health_mutation_rolls_back_if_coach_invalidation_fails(accounts, monkeypatch, operation):
    user_id, headers = accounts[0]
    revision = _connect(headers)["connectionRevision"]
    today = datetime.now(timezone.utc).date()
    day = {"localDate": today.isoformat(), "timeZone": "UTC", "steps": 5000, "asleepMinutes": 450}
    assert _days(headers, revision, days=[day]).status_code == 200
    assert client.put("/v1/users/me/health/preferences", headers=headers, json={
        "primarySource": "apple_health", "coachEnabled": True,
    }).status_code == 200

    def fail_invalidation(*args, **kwargs):
        raise RuntimeError("simulated invalidation failure")

    monkeypatch.setattr(health_service, "reconcile_after_mutation", fail_invalidation)
    with SessionLocal() as db, pytest.raises(RuntimeError, match="simulated invalidation failure"):
        if operation == "source":
            health_service.enable_source(db, user_id, HealthSourceRequest.model_validate({
                "source": "apple_health", "readEnabled": False, "exportEnabled": True,
            }))
        elif operation == "preferences":
            health_service.update_preferences(db, user_id, HealthPreferencesRequest.model_validate({
                "primarySource": "apple_health", "coachEnabled": False,
            }))
        elif operation == "days":
            health_service.upsert_days(db, user_id, HealthDaysRequest.model_validate({
                "source": "apple_health", "connectionRevision": revision,
                "days": [{**day, "steps": 9000}],
            }))
        else:
            health_service.disconnect_source(db, user_id, "apple_health")
    with SessionLocal() as db:
        source = db.query(HealthSource).filter_by(user_id=user_id, source="apple_health").one()
        prefs = db.get(HealthPreference, user_id)
        saved_day = db.query(HealthDay).filter_by(user_id=user_id, source="apple_health").one()
        assert source.enabled and source.read_enabled and not source.export_enabled
        assert source.connection_revision == revision
        assert prefs.primary_source == "apple_health" and prefs.coach_enabled
        assert saved_day.steps == 5000


def test_health_coach_ignores_missing_stale_and_boundary_values(accounts, monkeypatch):
    user_id, headers = accounts[0]
    today = datetime.now(timezone.utc).date()
    monkeypatch.setattr(
        coach_feed_service,
        "_now",
        lambda: datetime.combine(today, time(12), tzinfo=timezone.utc),
    )
    revision = _connect(headers)["connectionRevision"]
    assert client.put("/v1/users/me/health/preferences", headers=headers, json={
        "primarySource": "apple_health", "coachEnabled": True,
    }).status_code == 200
    days = _coach_days(
        today,
        sleep_baseline=[420] * 7,
        steps_baseline=[8000] * 7,
        observed_sleep=None,
        observed_steps=None,
    )
    assert _days(headers, revision, days=days).status_code == 200
    with SessionLocal() as db:
        assert coach_feed_service._health_context_candidates(db, user_id) == []
    days[-1]["asleepMinutes"] = 360
    days[-1]["steps"] = 12000
    assert _days(headers, revision, days=days).status_code == 200
    with SessionLocal() as db:
        assert coach_feed_service._health_context_candidates(db, user_id) == []
    days[-1]["asleepMinutes"] = None
    days[-1]["steps"] = 12001
    assert _days(headers, revision, days=days).status_code == 200
    with SessionLocal() as db:
        assert coach_feed_service._health_context_candidates(db, user_id)[0]["title"] == "Check in after a busy day"
    assert client.put("/v1/users/me/health/days", headers=headers, json={
        "source": "apple_health",
        "connectionRevision": revision,
        "days": [],
        "replaceWindow": {
            "startDate": (today - timedelta(days=1)).isoformat(),
            "endDate": (today - timedelta(days=1)).isoformat(),
            "timeZone": "UTC",
        },
    }).status_code == 200
    with SessionLocal() as db:
        assert coach_feed_service._health_context_candidates(db, user_id) == []
