"""
Tests for workout session logging endpoints:
- POST /v1/workouts/sessions
- POST /v1/workouts/sessions/{id}/sets
- PATCH /v1/workouts/sessions/{id}/complete
- GET /v1/workouts/sessions
- GET /v1/workouts/sessions/stats

Note: These tests assume migrations have been run and seed data exists in the dev DB.
"""
import datetime
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _unique_email(prefix: str) -> str:
    """Generate a truly unique email for each test run."""
    return f"logging_{prefix}_{uuid.uuid4().hex[:8]}@example.com"


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


# ── Auth guards ───────────────────────────────────────────────────────────────

def test_create_session_requires_auth():
    resp = client.post("/v1/workouts/sessions", json={
        "workoutDayId": "x", "workoutDate": "2026-01-01", "dayType": "upper"
    })
    assert resp.status_code == 401


def test_log_set_requires_auth():
    resp = client.post("/v1/workouts/sessions/fake-id/sets", json={
        "exerciseId": "push_up", "setNumber": 1, "reps": 10
    })
    assert resp.status_code == 401


def test_stats_requires_auth():
    resp = client.get("/v1/workouts/sessions/stats")
    assert resp.status_code == 401


# ── Session creation ──────────────────────────────────────────────────────────

def test_create_session_returns_correct_shape():
    token = _signup_and_get_token(_unique_email("create"))
    session = _create_session(token)

    assert "id" in session
    assert session["workoutDayId"] == "day-001"
    assert session["dayType"] == "upper"
    assert session["completedAt"] is None
    assert session["setLogs"] == []


def test_create_session_stores_today():
    token = _signup_and_get_token(_unique_email("date"))
    session = _create_session(token)
    assert session["workoutDate"] == datetime.date.today().isoformat()


# ── Set logging ───────────────────────────────────────────────────────────────

def test_log_set_success():
    token = _signup_and_get_token(_unique_email("logset"))
    session = _create_session(token)
    session_id = session["id"]

    resp = client.post(
        f"/v1/workouts/sessions/{session_id}/sets",
        json={"exerciseId": "push_up", "setNumber": 1, "reps": 10},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["exerciseId"] == "push_up"
    assert data["reps"] == 10
    assert data["weightKg"] is None


def test_log_set_with_weight():
    token = _signup_and_get_token(_unique_email("weight"))
    session = _create_session(token)

    resp = client.post(
        f"/v1/workouts/sessions/{session['id']}/sets",
        json={"exerciseId": "push_up", "setNumber": 1, "reps": 8, "weightKg": 60.0},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    assert resp.json()["weightKg"] == 60.0


def test_log_set_to_another_users_session_returns_404():
    """A user cannot log sets to another user's session."""
    token_a = _signup_and_get_token(_unique_email("owner"))
    token_b = _signup_and_get_token(_unique_email("intruder"))

    session = _create_session(token_a)

    resp = client.post(
        f"/v1/workouts/sessions/{session['id']}/sets",
        json={"exerciseId": "push_up", "setNumber": 1, "reps": 5},
        headers=_auth(token_b),
    )
    assert resp.status_code == 404


def test_log_set_to_completed_session_returns_409():
    """Cannot add sets to an already-completed session."""
    token = _signup_and_get_token(_unique_email("completed"))
    session = _create_session(token)
    session_id = session["id"]

    # Complete it
    client.patch(f"/v1/workouts/sessions/{session_id}/complete", headers=_auth(token))

    # Try to log a set
    resp = client.post(
        f"/v1/workouts/sessions/{session_id}/sets",
        json={"exerciseId": "push_up", "setNumber": 1, "reps": 5},
        headers=_auth(token),
    )
    assert resp.status_code == 409


# ── Session completion ────────────────────────────────────────────────────────

def test_complete_session_sets_completed_at():
    token = _signup_and_get_token(_unique_email("complete"))
    session = _create_session(token)
    session_id = session["id"]

    resp = client.patch(
        f"/v1/workouts/sessions/{session_id}/complete",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["completedAt"] is not None


def test_complete_session_is_idempotent():
    """Completing an already-completed session returns 200 without error."""
    token = _signup_and_get_token(_unique_email("idempotent"))
    session = _create_session(token)
    session_id = session["id"]

    client.patch(f"/v1/workouts/sessions/{session_id}/complete", headers=_auth(token))
    resp = client.patch(f"/v1/workouts/sessions/{session_id}/complete", headers=_auth(token))
    assert resp.status_code == 200


def test_complete_another_users_session_returns_404():
    token_a = _signup_and_get_token(_unique_email("owner2"))
    token_b = _signup_and_get_token(_unique_email("intruder2"))

    session = _create_session(token_a)
    resp = client.patch(
        f"/v1/workouts/sessions/{session['id']}/complete",
        headers=_auth(token_b),
    )
    assert resp.status_code == 404


# ── Session list ──────────────────────────────────────────────────────────────

def test_list_sessions_returns_only_own_sessions():
    token_a = _signup_and_get_token(_unique_email("list_a"))
    token_b = _signup_and_get_token(_unique_email("list_b"))

    _create_session(token_a, workout_day_id="a-day")
    _create_session(token_b, workout_day_id="b-day")

    resp = client.get("/v1/workouts/sessions", headers=_auth(token_a))
    assert resp.status_code == 200
    data = resp.json()
    assert all(item["workoutDayId"] == "a-day" for item in data["items"])


def test_list_sessions_includes_set_count():
    token = _signup_and_get_token(_unique_email("setcount"))
    session = _create_session(token)

    # Log 3 sets
    for i in range(1, 4):
        client.post(
            f"/v1/workouts/sessions/{session['id']}/sets",
            json={"exerciseId": "push_up", "setNumber": i, "reps": 10},
            headers=_auth(token),
        )

    resp = client.get("/v1/workouts/sessions", headers=_auth(token))
    assert resp.json()["items"][0]["setCount"] == 3


# ── Stats ─────────────────────────────────────────────────────────────────────

def test_stats_zero_for_new_user():
    token = _signup_and_get_token(_unique_email("stats_zero"))
    resp = client.get("/v1/workouts/sessions/stats", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["streak"] == 0
    assert data["totalCompleted"] == 0
    assert data["prsThisWeek"] == 0
    assert data["totalVolumeKg"] == 0


def test_stats_increments_after_completion():
    token = _signup_and_get_token(_unique_email("stats_incr"))
    session = _create_session(token)
    client.patch(f"/v1/workouts/sessions/{session['id']}/complete", headers=_auth(token))

    resp = client.get("/v1/workouts/sessions/stats", headers=_auth(token))
    data = resp.json()
    assert data["totalCompleted"] == 1
    assert data["streak"] == 1


def test_stats_pr_detected_for_new_weight():
    token = _signup_and_get_token(_unique_email("stats_pr"))
    session = _create_session(token)

    # Log a weighted set
    client.post(
        f"/v1/workouts/sessions/{session['id']}/sets",
        json={"exerciseId": "push_up", "setNumber": 1, "reps": 8, "weightKg": 50.0},
        headers=_auth(token),
    )
    client.patch(f"/v1/workouts/sessions/{session['id']}/complete", headers=_auth(token))

    resp = client.get("/v1/workouts/sessions/stats", headers=_auth(token))
    # No prior history → first-ever weight counts as PR
    assert resp.json()["prsThisWeek"] == 1


def _log_set(token: str, session_id: str, set_number: int, reps: int, weight_kg=None):
    payload = {"exerciseId": "push_up", "setNumber": set_number, "reps": reps}
    if weight_kg is not None:
        payload["weightKg"] = weight_kg
    resp = client.post(
        f"/v1/workouts/sessions/{session_id}/sets", json=payload, headers=_auth(token)
    )
    assert resp.status_code == 201


def test_stats_volume_sums_weight_times_reps():
    token = _signup_and_get_token(_unique_email("vol_sum"))
    session = _create_session(token)

    _log_set(token, session["id"], 1, reps=10, weight_kg=60.0)
    _log_set(token, session["id"], 2, reps=8, weight_kg=62.5)
    client.patch(f"/v1/workouts/sessions/{session['id']}/complete", headers=_auth(token))

    resp = client.get("/v1/workouts/sessions/stats", headers=_auth(token))
    # 60 x 10 + 62.5 x 8 = 1100
    assert resp.json()["totalVolumeKg"] == 1100


def test_stats_volume_excludes_sets_logged_without_weight():
    """Bodyweight sets carry no weight, so they must not count as zero-weight reps."""
    token = _signup_and_get_token(_unique_email("vol_bw"))
    session = _create_session(token)

    _log_set(token, session["id"], 1, reps=20)
    _log_set(token, session["id"], 2, reps=5, weight_kg=40.0)
    client.patch(f"/v1/workouts/sessions/{session['id']}/complete", headers=_auth(token))

    resp = client.get("/v1/workouts/sessions/stats", headers=_auth(token))
    assert resp.json()["totalVolumeKg"] == 200


def test_stats_volume_ignores_sessions_that_were_never_completed():
    token = _signup_and_get_token(_unique_email("vol_open"))
    session = _create_session(token)

    _log_set(token, session["id"], 1, reps=10, weight_kg=100.0)

    resp = client.get("/v1/workouts/sessions/stats", headers=_auth(token))
    assert resp.json()["totalVolumeKg"] == 0


def test_stats_volume_is_isolated_per_user():
    token_a = _signup_and_get_token(_unique_email("vol_a"))
    token_b = _signup_and_get_token(_unique_email("vol_b"))

    session_a = _create_session(token_a)
    _log_set(token_a, session_a["id"], 1, reps=10, weight_kg=50.0)
    client.patch(f"/v1/workouts/sessions/{session_a['id']}/complete", headers=_auth(token_a))

    assert client.get("/v1/workouts/sessions/stats", headers=_auth(token_a)).json()[
        "totalVolumeKg"
    ] == 500
    assert client.get("/v1/workouts/sessions/stats", headers=_auth(token_b)).json()[
        "totalVolumeKg"
    ] == 0
