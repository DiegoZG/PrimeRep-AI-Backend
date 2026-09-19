"""Contract tests for resumable, idempotent workout sessions."""
import datetime
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.workout_session import WorkoutSession
from conftest import LEGAL_ACCEPTANCE
from workout_test_utils import install_startable_workout

client = TestClient(app)


def _token() -> str:
    response = client.post(
        "/v1/auth/signup",
        json={
            "email": f"resume_{uuid.uuid4().hex[:10]}@example.com",
            "password": "StrongPass123",
            "preferred_name": "Resume",
            "last_name": "Test",
            "legalAcceptance": LEGAL_ACCEPTANCE,
        },
    )
    assert response.status_code == 201
    return response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _start(token: str, client_session_id: Optional[str] = None) -> dict:
    workout = install_startable_workout(
        client, token, workout_day_id="resumable-day"
    )
    response = client.post(
        "/v1/workouts/sessions",
        headers=_headers(token),
        json={
            "workoutDayId": "resumable-day",
            "workoutDate": workout["date"],
            "dayType": workout["dayType"],
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
    repeat = client.patch(f"/v1/workouts/sessions/{completed['id']}/complete", headers=_headers(token))
    assert repeat.status_code == 200
    assert repeat.json()["id"] == response.json()["id"]
    assert repeat.json()["completedAt"] == response.json()["completedAt"]
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


def test_set_must_belong_to_immutable_workout_snapshot():
    token = _token()
    week = client.get("/v1/workouts/week", headers=_headers(token)).json()
    workout = week["workouts"][0]
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
    allowed_exercise_id = session.json()["workoutSnapshot"]["exerciseBlocks"][0]["items"][0]["exercise"]["id"]

    allowed = client.post(
        f"/v1/workouts/sessions/{session.json()['id']}/sets",
        headers=_headers(token),
        json={
            "exerciseId": allowed_exercise_id,
            "setNumber": 1,
            "reps": 8,
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert allowed.status_code == 201

    response = client.post(
        f"/v1/workouts/sessions/{session.json()['id']}/sets",
        headers=_headers(token),
        json={
            "exerciseId": "not-an-exercise-in-this-workout",
            "setNumber": 1,
            "reps": 8,
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Exercise is not part of this workout."


def test_stale_workout_id_cannot_create_a_snapshotless_session():
    token = _token()
    headers = _headers(token)
    workout = install_startable_workout(
        client, token, workout_day_id="replaced-workout"
    )
    refreshed = client.get(
        "/v1/workouts/week",
        headers=headers,
        params={"weekStart": workout["date"], "force": "true"},
    )
    assert refreshed.status_code == 200
    response = client.post(
        "/v1/workouts/sessions",
        headers=headers,
        json={
            "workoutDayId": workout["workoutDayId"],
            "workoutDate": workout["date"],
            "dayType": "client-controlled-value",
            "clientSessionId": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 404
    assert "no longer available" in response.json()["detail"]
    user_id = client.get("/v1/users/me", headers=headers).json()["id"]
    with SessionLocal() as db:
        assert db.query(WorkoutSession).filter_by(user_id=user_id).count() == 0


def test_start_and_activation_share_schedule_lock_and_never_create_null_snapshot():
    token = _token()
    headers = _headers(token)
    assert client.post(
        "/v1/onboarding/me",
        headers=headers,
        json={
            "data": {
                "selectedEquipment": ["dumbbells", "pull_up_bar", "dip_bar"]
            },
            "is_complete": True,
        },
    ).status_code == 200
    week = client.get("/v1/workouts/week", headers=headers).json()
    workout = week["workouts"][0]
    monday = datetime.date.fromisoformat(week["weekStart"])
    preview_payload = {
        "weekStart": week["weekStart"],
        "effectiveDate": monday.isoformat(),
        "weekdays": [0, 2, 4],
        "applyMode": "now",
    }
    preview = client.post(
        "/v1/workout-templates/system-minimal-equipment/activation-preview",
        headers=headers,
        json=preview_payload,
    )
    assert preview.status_code == 200
    substitutions = {
        item["templateExerciseId"]: item["suggestedExerciseId"]
        for item in preview.json()["equipmentConflicts"]
        if item["suggestedExerciseId"]
    }

    def start():
        return client.post(
            "/v1/workouts/sessions",
            headers=headers,
            json={
                "workoutDayId": workout["workoutDayId"],
                "workoutDate": workout["date"],
                "dayType": workout["dayType"],
                "clientSessionId": str(uuid.uuid4()),
            },
        )

    def activate():
        return client.post(
            "/v1/workout-templates/system-minimal-equipment/activate",
            headers=headers,
            json={
                **preview_payload,
                "templateVersion": 1,
                "substitutions": substitutions,
                "clientOperationId": str(uuid.uuid4()),
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        start_future = executor.submit(start)
        activate_future = executor.submit(activate)
        started = start_future.result(timeout=10)
        activated = activate_future.result(timeout=10)
    assert activated.status_code == 200, activated.json()
    assert started.status_code in {201, 404}, started.json()
    user_id = client.get("/v1/users/me", headers=headers).json()["id"]
    with SessionLocal() as db:
        sessions = db.query(WorkoutSession).filter_by(user_id=user_id).all()
        assert all(session.workout_snapshot is not None for session in sessions)
