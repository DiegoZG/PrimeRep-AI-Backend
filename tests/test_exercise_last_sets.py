"""
Tests for GET /v1/exercises/{exercise_id}/last-sets

Note: These tests assume migrations have been run and seed data exists in the dev DB.
"""
import datetime
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _unique_email(prefix: str) -> str:
    """Generate a truly unique email for each test run."""
    return f"lastsets_{prefix}_{uuid.uuid4().hex[:8]}@example.com"


def _signup_and_get_token(email: str) -> str:
    payload = {
        "email": email,
        "password": "StrongPass123",
        "preferred_name": "Test",
        "last_name": "User",
    }
    resp = client.post("/v1/auth/signup", json=payload)
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_session(token: str, workout_day_id: str = "day-001") -> dict:
    payload = {
        "workoutDayId": workout_day_id,
        "workoutDate": datetime.date.today().isoformat(),
        "dayType": "upper",
    }
    resp = client.post("/v1/workouts/sessions", json=payload, headers=_auth(token))
    assert resp.status_code == 201
    return resp.json()


def _log_set(
    token: str,
    session_id: str,
    exercise_id: str = "push_up",
    set_number: int = 1,
    reps: int = 10,
    weight_kg=None,
) -> dict:
    payload = {"exerciseId": exercise_id, "setNumber": set_number, "reps": reps}
    if weight_kg is not None:
        payload["weightKg"] = weight_kg
    resp = client.post(
        f"/v1/workouts/sessions/{session_id}/sets",
        json=payload,
        headers=_auth(token),
    )
    assert resp.status_code == 201
    return resp.json()


def _complete_session(token: str, session_id: str) -> None:
    resp = client.patch(f"/v1/workouts/sessions/{session_id}/complete", headers=_auth(token))
    assert resp.status_code == 200


# ── Auth guard ────────────────────────────────────────────────────────────────

def test_last_sets_requires_auth():
    resp = client.get("/v1/exercises/push_up/last-sets")
    assert resp.status_code == 401


# ── 404 for unknown exercise ─────────────────────────────────────────────────

def test_last_sets_unknown_exercise_returns_404():
    token = _signup_and_get_token(_unique_email("unknown_ex"))
    resp = client.get("/v1/exercises/not-a-real-exercise/last-sets", headers=_auth(token))
    assert resp.status_code == 404


# ── Empty history ─────────────────────────────────────────────────────────────

def test_last_sets_empty_history_returns_empty_items():
    token = _signup_and_get_token(_unique_email("empty"))
    resp = client.get("/v1/exercises/push_up/last-sets", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


# ── Completed sessions only ──────────────────────────────────────────────────

def test_last_sets_excludes_sets_from_incomplete_sessions():
    token = _signup_and_get_token(_unique_email("incomplete"))
    session = _create_session(token)
    _log_set(token, session["id"])

    resp = client.get("/v1/exercises/push_up/last-sets", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_last_sets_returns_sets_from_completed_sessions():
    token = _signup_and_get_token(_unique_email("completed"))
    session = _create_session(token)
    _log_set(token, session["id"], reps=8, weight_kg=50.0)
    _complete_session(token, session["id"])

    resp = client.get("/v1/exercises/push_up/last-sets", headers=_auth(token))
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["exerciseId"] == "push_up"
    assert items[0]["reps"] == 8
    assert items[0]["weightKg"] == 50.0
    assert "setNumber" in items[0]
    assert "loggedAt" in items[0]
    assert "id" in items[0]


# ── Cross-user isolation ──────────────────────────────────────────────────────

def test_last_sets_cross_user_isolation():
    token_a = _signup_and_get_token(_unique_email("owner"))
    token_b = _signup_and_get_token(_unique_email("intruder"))

    session_a = _create_session(token_a)
    _log_set(token_a, session_a["id"], reps=12)
    _complete_session(token_a, session_a["id"])

    resp = client.get("/v1/exercises/push_up/last-sets", headers=_auth(token_b))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ── Ordering ──────────────────────────────────────────────────────────────────

def test_last_sets_ordered_newest_first():
    token = _signup_and_get_token(_unique_email("order"))

    session1 = _create_session(token, workout_day_id="day-1")
    _log_set(token, session1["id"], set_number=1, reps=10, weight_kg=40.0)
    _complete_session(token, session1["id"])

    session2 = _create_session(token, workout_day_id="day-2")
    _log_set(token, session2["id"], set_number=1, reps=10, weight_kg=45.0)
    _complete_session(token, session2["id"])

    resp = client.get("/v1/exercises/push_up/last-sets?limit=10", headers=_auth(token))
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    # Newest (session2, weight 45) should come first
    assert items[0]["weightKg"] == 45.0
    assert items[1]["weightKg"] == 40.0


# ── Limit ─────────────────────────────────────────────────────────────────────

def test_last_sets_respects_limit():
    token = _signup_and_get_token(_unique_email("limit"))
    session = _create_session(token)

    for i in range(1, 6):
        _log_set(token, session["id"], set_number=i, reps=10)
    _complete_session(token, session["id"])

    resp = client.get("/v1/exercises/push_up/last-sets?limit=3", headers=_auth(token))
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 3


def test_last_sets_default_limit_is_three():
    token = _signup_and_get_token(_unique_email("default_limit"))
    session = _create_session(token)

    for i in range(1, 6):
        _log_set(token, session["id"], set_number=i, reps=10)
    _complete_session(token, session["id"])

    resp = client.get("/v1/exercises/push_up/last-sets", headers=_auth(token))
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 3


def test_last_sets_limit_out_of_range_rejected():
    token = _signup_and_get_token(_unique_email("bad_limit"))
    resp = client.get("/v1/exercises/push_up/last-sets?limit=0", headers=_auth(token))
    assert resp.status_code == 422

    resp = client.get("/v1/exercises/push_up/last-sets?limit=21", headers=_auth(token))
    assert resp.status_code == 422
