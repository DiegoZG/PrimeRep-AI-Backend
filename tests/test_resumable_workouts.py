"""Contract tests for resumable, idempotent workout sessions."""
import datetime
import uuid
from typing import Optional

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _token() -> str:
    response = client.post(
        "/v1/auth/signup",
        json={
            "email": f"resume_{uuid.uuid4().hex[:10]}@example.com",
            "password": "StrongPass123",
            "preferred_name": "Resume",
            "last_name": "Test",
        },
    )
    assert response.status_code == 201
    return response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _start(token: str, client_session_id: Optional[str] = None) -> dict:
    response = client.post(
        "/v1/workouts/sessions",
        headers=_headers(token),
        json={
            "workoutDayId": "resumable-day",
            "workoutDate": datetime.date.today().isoformat(),
            "dayType": "upper",
            "clientSessionId": client_session_id or str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201
    return response.json()


def test_client_session_id_is_idempotent_and_active_lookup_is_private():
    token = _token()
    client_session_id = str(uuid.uuid4())
    session = _start(token, client_session_id)

    repeat = client.post(
        "/v1/workouts/sessions",
        headers=_headers(token),
        json={
            "workoutDayId": "resumable-day",
            "workoutDate": datetime.date.today().isoformat(),
            "dayType": "upper",
            "clientSessionId": client_session_id,
        },
    )
    assert repeat.status_code == 200
    assert repeat.json()["id"] == session["id"]
    assert session["status"] == "in_progress"

    active = client.get("/v1/workouts/sessions/active", headers=_headers(token))
    assert active.status_code == 200
    assert active.json()["id"] == session["id"]

    other = _token()
    assert client.get("/v1/workouts/sessions/active", headers=_headers(other)).json() is None
    assert client.get(f"/v1/workouts/sessions/{session['id']}", headers=_headers(other)).status_code == 404


def test_set_operation_id_is_idempotent_and_session_detail_includes_sets():
    token = _token()
    session = _start(token)
    operation_id = str(uuid.uuid4())
    payload = {"exerciseId": "push_up", "setNumber": 1, "reps": 8, "clientOperationId": operation_id}
    first = client.post(f"/v1/workouts/sessions/{session['id']}/sets", json=payload, headers=_headers(token))
    second = client.post(f"/v1/workouts/sessions/{session['id']}/sets", json=payload, headers=_headers(token))
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    detail = client.get(f"/v1/workouts/sessions/{session['id']}", headers=_headers(token))
    assert len(detail.json()["setLogs"]) == 1


def test_complete_and_abandon_remove_session_from_active_lookup():
    token = _token()
    completed = _start(token)
    response = client.patch(f"/v1/workouts/sessions/{completed['id']}/complete", headers=_headers(token))
    assert response.json()["status"] == "completed"
    assert client.get("/v1/workouts/sessions/active", headers=_headers(token)).json() is None


def test_client_identifiers_are_scoped_to_their_user_or_session():
    token_a = _token()
    token_b = _token()
    shared_session_id = str(uuid.uuid4())
    first = _start(token_a, shared_session_id)
    second = _start(token_b, shared_session_id)
    assert first["id"] != second["id"]

    operation_id = str(uuid.uuid4())
    first_set = client.post(
        f"/v1/workouts/sessions/{first['id']}/sets",
        json={"exerciseId": "push_up", "setNumber": 1, "reps": 8, "clientOperationId": operation_id},
        headers=_headers(token_a),
    )
    assert first_set.status_code == 201
    assert client.patch(f"/v1/workouts/sessions/{first['id']}/complete", headers=_headers(token_a)).status_code == 200
    next_session = _start(token_a)
    second_set = client.post(
        f"/v1/workouts/sessions/{next_session['id']}/sets",
        json={"exerciseId": "push_up", "setNumber": 1, "reps": 9, "clientOperationId": operation_id},
        headers=_headers(token_a),
    )
    assert second_set.status_code == 201
    assert first_set.json()["id"] != second_set.json()["id"]


def test_only_one_active_session_and_terminal_transitions_are_enforced():
    token = _token()
    active = _start(token)
    conflict = client.post(
        "/v1/workouts/sessions",
        headers=_headers(token),
        json={
            "workoutDayId": "other-day",
            "workoutDate": datetime.date.today().isoformat(),
            "dayType": "lower",
            "clientSessionId": str(uuid.uuid4()),
        },
    )
    assert conflict.status_code == 409

    abandoned = client.patch(f"/v1/workouts/sessions/{active['id']}/abandon", headers=_headers(token))
    assert abandoned.status_code == 200
    assert client.patch(f"/v1/workouts/sessions/{active['id']}/abandon", headers=_headers(token)).status_code == 200
    assert client.patch(f"/v1/workouts/sessions/{active['id']}/complete", headers=_headers(token)).status_code == 409

    completed = _start(token)
    assert client.patch(f"/v1/workouts/sessions/{completed['id']}/complete", headers=_headers(token)).status_code == 200
    assert client.patch(f"/v1/workouts/sessions/{completed['id']}/complete", headers=_headers(token)).status_code == 200
    assert client.patch(f"/v1/workouts/sessions/{completed['id']}/abandon", headers=_headers(token)).status_code == 409

    abandoned = _start(token)
    response = client.patch(f"/v1/workouts/sessions/{abandoned['id']}/abandon", headers=_headers(token))
    assert response.json()["status"] == "abandoned"
    late_set = client.post(
        f"/v1/workouts/sessions/{abandoned['id']}/sets",
        json={"exerciseId": "push_up", "setNumber": 1, "reps": 8, "clientOperationId": str(uuid.uuid4())},
        headers=_headers(token),
    )
    assert late_set.status_code == 409
    assert client.get("/v1/workouts/sessions/active", headers=_headers(token)).json() is None


def test_active_session_keeps_snapshot_after_its_day_is_skipped():
    token = _token()
    week = client.get("/v1/workouts/week", headers=_headers(token))
    assert week.status_code == 200
    workout = week.json()["workouts"][0]
    session = client.post(
        "/v1/workouts/sessions",
        headers=_headers(token),
        json={
            "workoutDayId": workout["workoutDayId"],
            "workoutDate": workout["date"],
            "dayType": workout["dayType"],
            "clientSessionId": str(uuid.uuid4()),
        },
    )
    assert session.status_code == 201
    assert session.json()["workoutSnapshot"]["title"] == workout["title"]
    assert session.json()["recoveryRequired"] is False

    skipped = client.post(
        "/v1/workouts/week/skip",
        headers=_headers(token),
        json={"workoutDayId": workout["workoutDayId"]},
    )
    assert skipped.status_code == 200
    assert workout["workoutDayId"] not in {item["workoutDayId"] for item in skipped.json()["workouts"]}

    active = client.get("/v1/workouts/sessions/active", headers=_headers(token))
    assert active.status_code == 200
    assert active.json()["id"] == session.json()["id"]
    assert active.json()["workoutSnapshot"] == session.json()["workoutSnapshot"]
