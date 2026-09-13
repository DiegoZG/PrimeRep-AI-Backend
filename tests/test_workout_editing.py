"""Regression tests for auditable workout corrections and notes."""
import datetime
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _token() -> str:
    response = client.post(
        "/v1/auth/signup",
        json={
            "email": f"editing_{uuid.uuid4().hex[:10]}@example.com",
            "password": "StrongPass123",
            "preferred_name": "Editing",
            "last_name": "Test",
        },
    )
    assert response.status_code == 201
    return response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _session(token: str) -> dict:
    response = client.post(
        "/v1/workouts/sessions",
        headers=_headers(token),
        json={
            "workoutDayId": "editing-day",
            "workoutDate": datetime.date.today().isoformat(),
            "dayType": "upper",
            "clientSessionId": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201
    return response.json()


def _set(token: str, session_id: str, set_number: int = 1) -> dict:
    response = client.post(
        f"/v1/workouts/sessions/{session_id}/sets",
        headers=_headers(token),
        json={
            "exerciseId": "push_up",
            "setNumber": set_number,
            "reps": 10,
            "weightKg": 20,
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201
    return response.json()


def test_update_delete_and_restore_are_idempotent_and_recalculate_effective_sets():
    token = _token()
    session = _session(token)
    logged = _set(token, session["id"])

    update_id = str(uuid.uuid4())
    payload = {"reps": 12, "weightKg": None, "expectedVersion": logged["version"], "clientOperationId": update_id}
    first = client.patch(f"/v1/workouts/sessions/{session['id']}/sets/{logged['id']}", headers=_headers(token), json=payload)
    again = client.patch(f"/v1/workouts/sessions/{session['id']}/sets/{logged['id']}", headers=_headers(token), json=payload)
    assert first.status_code == again.status_code == 200
    assert first.json()["reps"] == again.json()["reps"] == 12
    assert first.json()["weightKg"] is None

    delete_id = str(uuid.uuid4())
    deleted = client.delete(
        f"/v1/workouts/sessions/{session['id']}/sets/{logged['id']}?clientOperationId={delete_id}", headers=_headers(token)
    )
    repeat_delete = client.delete(
        f"/v1/workouts/sessions/{session['id']}/sets/{logged['id']}?clientOperationId={delete_id}", headers=_headers(token)
    )
    assert deleted.status_code == repeat_delete.status_code == 204
    detail = client.get(f"/v1/workouts/sessions/{session['id']}", headers=_headers(token)).json()
    assert detail["setLogs"] == []
    assert [(set_log["id"], set_log["exerciseId"], set_log["setNumber"]) for set_log in detail["deletedSetLogs"]] == [
        (logged["id"], "push_up", 1)
    ]

    restored = client.post(
        f"/v1/workouts/sessions/{session['id']}/sets/{logged['id']}/restore",
        headers=_headers(token),
        json={"clientOperationId": str(uuid.uuid4())},
    )
    assert restored.status_code == 200
    assert restored.json()["id"] == logged["id"]


def test_mutation_operation_id_cannot_be_reused_for_another_set():
    token = _token()
    session = _session(token)
    first_set = _set(token, session["id"], set_number=1)
    second_set = _set(token, session["id"], set_number=2)
    operation_id = str(uuid.uuid4())

    first = client.patch(
        f"/v1/workouts/sessions/{session['id']}/sets/{first_set['id']}",
        headers=_headers(token),
        json={
            "reps": 12,
            "expectedVersion": first_set["version"],
            "clientOperationId": operation_id,
        },
    )
    reused = client.patch(
        f"/v1/workouts/sessions/{session['id']}/sets/{second_set['id']}",
        headers=_headers(token),
        json={
            "reps": 11,
            "expectedVersion": second_set["version"],
            "clientOperationId": operation_id,
        },
    )

    assert first.status_code == 200
    assert reused.status_code == 409
    detail = client.get(f"/v1/workouts/sessions/{session['id']}", headers=_headers(token))
    assert detail.status_code == 200
    assert {set_log["id"]: set_log["reps"] for set_log in detail.json()["setLogs"]} == {
        first_set["id"]: 12,
        second_set["id"]: 10,
    }


def test_duplicate_active_set_slot_is_rejected_but_deleted_slot_can_be_restored():
    token = _token()
    session = _session(token)
    logged = _set(token, session["id"])
    duplicate = client.post(
        f"/v1/workouts/sessions/{session['id']}/sets",
        headers=_headers(token),
        json={"exerciseId": "push_up", "setNumber": 1, "reps": 8, "clientOperationId": str(uuid.uuid4())},
    )
    assert duplicate.status_code == 409
    assert client.delete(
        f"/v1/workouts/sessions/{session['id']}/sets/{logged['id']}?clientOperationId={uuid.uuid4()}", headers=_headers(token)
    ).status_code == 204
    replacement = client.post(
        f"/v1/workouts/sessions/{session['id']}/sets",
        headers=_headers(token),
        json={"exerciseId": "push_up", "setNumber": 1, "reps": 8, "clientOperationId": str(uuid.uuid4())},
    )
    assert replacement.status_code == 201


def test_completed_workouts_are_editable_but_abandoned_ones_are_read_only():
    token = _token()
    completed = _session(token)
    logged = _set(token, completed["id"])
    assert client.patch(f"/v1/workouts/sessions/{completed['id']}/complete", headers=_headers(token)).status_code == 200
    assert client.patch(
        f"/v1/workouts/sessions/{completed['id']}/sets/{logged['id']}",
        headers=_headers(token),
        json={"reps": 8, "expectedVersion": logged["version"], "clientOperationId": str(uuid.uuid4())},
    ).status_code == 200

    abandoned = _session(token)
    abandoned_log = _set(token, abandoned["id"])
    assert client.patch(f"/v1/workouts/sessions/{abandoned['id']}/abandon", headers=_headers(token)).status_code == 200
    assert client.patch(
        f"/v1/workouts/sessions/{abandoned['id']}/sets/{abandoned_log['id']}",
        headers=_headers(token),
        json={"reps": 8, "expectedVersion": abandoned_log["version"], "clientOperationId": str(uuid.uuid4())},
    ).status_code == 409


def test_completed_session_detail_retains_effective_sets_summary_and_note():
    token = _token()
    session = _session(token)
    logged = _set(token, session["id"])
    note = client.put(
        f"/v1/workouts/sessions/{session['id']}/note",
        headers=_headers(token),
        json={"note": "Controlled tempo on every rep.", "clientOperationId": str(uuid.uuid4())},
    )
    assert note.status_code == 200

    completed = client.patch(f"/v1/workouts/sessions/{session['id']}/complete", headers=_headers(token))
    assert completed.status_code == 200
    body = completed.json()
    assert [(item["id"], item["reps"], item["weightKg"]) for item in body["setLogs"]] == [
        (logged["id"], 10, 20)
    ]
    assert body["summary"]["completedSetCount"] == 1
    assert body["summary"]["totalVolumeKg"] == 200
    assert body["workoutNote"] == "Controlled tempo on every rep."

    detail = client.get(f"/v1/workouts/sessions/{session['id']}", headers=_headers(token))
    assert detail.status_code == 200
    assert detail.json()["setLogs"] == body["setLogs"]
    assert detail.json()["summary"] == body["summary"]
    assert detail.json()["workoutNote"] == body["workoutNote"]


def test_workout_note_saved_after_completion_survives_fresh_detail_request():
    token = _token()
    session = _session(token)
    _set(token, session["id"])
    assert client.patch(f"/v1/workouts/sessions/{session['id']}/complete", headers=_headers(token)).status_code == 200

    saved = client.put(
        f"/v1/workouts/sessions/{session['id']}/note",
        headers=_headers(token),
        json={"note": "Review this tomorrow.", "clientOperationId": str(uuid.uuid4())},
    )
    assert saved.status_code == 200
    assert saved.json()["workoutNote"] == "Review this tomorrow."

    detail = client.get(f"/v1/workouts/sessions/{session['id']}", headers=_headers(token))
    assert detail.status_code == 200
    assert detail.json()["workoutNote"] == "Review this tomorrow."


def test_stale_set_update_is_rejected_without_overwriting_newer_correction():
    token = _token()
    session = _session(token)
    logged = _set(token, session["id"])
    first = client.patch(
        f"/v1/workouts/sessions/{session['id']}/sets/{logged['id']}",
        headers=_headers(token),
        json={"reps": 11, "expectedVersion": logged["version"], "clientOperationId": str(uuid.uuid4())},
    )
    assert first.status_code == 200
    stale = client.patch(
        f"/v1/workouts/sessions/{session['id']}/sets/{logged['id']}",
        headers=_headers(token),
        json={"reps": 7, "expectedVersion": logged["version"], "clientOperationId": str(uuid.uuid4())},
    )
    assert stale.status_code == 409
    assert client.get(f"/v1/workouts/sessions/{session['id']}", headers=_headers(token)).json()["setLogs"][0]["reps"] == 11


def test_completed_history_and_stats_use_effective_non_deleted_sets():
    token = _token()
    session = _session(token)
    logged = _set(token, session["id"])
    assert client.patch(f"/v1/workouts/sessions/{session['id']}/complete", headers=_headers(token)).status_code == 200
    history = client.get("/v1/workouts/sessions/history", headers=_headers(token))
    assert history.status_code == 200
    assert history.json()["items"][0]["setCount"] == 1
    assert history.json()["items"][0]["totalVolumeKg"] == 200

    assert client.delete(
        f"/v1/workouts/sessions/{session['id']}/sets/{logged['id']}?clientOperationId={uuid.uuid4()}", headers=_headers(token)
    ).status_code == 204
    corrected = client.get(f"/v1/workouts/sessions/{session['id']}", headers=_headers(token)).json()
    assert corrected["summary"]["completedSetCount"] == 0
    assert corrected["summary"]["totalVolumeKg"] == 0
    assert client.get("/v1/workouts/sessions/stats", headers=_headers(token)).json()["totalVolumeKg"] == 0


def test_notes_and_feedback_are_private_and_session_scoped():
    token = _token()
    other = _token()
    week = client.get("/v1/workouts/week", headers=_headers(token)).json()
    workout = week["workouts"][0]
    started = client.post(
        "/v1/workouts/sessions",
        headers=_headers(token),
        json={
            "workoutDayId": workout["workoutDayId"],
            "workoutDate": workout["date"],
            "dayType": workout["dayType"],
            "clientSessionId": str(uuid.uuid4()),
        },
    )
    assert started.status_code == 201
    session = started.json()
    exercise_id = session["workoutSnapshot"]["exerciseBlocks"][0]["items"][0]["exercise"]["id"]
    note = client.put(
        f"/v1/workouts/sessions/{session['id']}/note",
        headers=_headers(token),
        json={"note": "Keep shoulders down", "clientOperationId": str(uuid.uuid4())},
    )
    assert note.status_code == 200
    assert note.json()["workoutNote"] == "Keep shoulders down"
    feedback = client.put(
        f"/v1/workouts/sessions/{session['id']}/exercises/{exercise_id}/feedback",
        headers=_headers(token),
        json={"effort": "right", "rir": 2, "clientOperationId": str(uuid.uuid4())},
    )
    assert feedback.status_code == 200
    assert feedback.json()["effort"] == "right"
    assert client.put(
        f"/v1/users/me/exercises/{exercise_id}/note", headers=_headers(token), json={"note": "Use handles"}
    ).status_code == 200
    assert client.get(f"/v1/users/me/exercises/{exercise_id}/note", headers=_headers(other)).json() is None


def test_persistent_exercise_notes_reject_unknown_exercises():
    token = _token()
    response = client.put(
        "/v1/users/me/exercises/not-a-real-exercise/note",
        headers=_headers(token),
        json={"note": "Use a slower tempo"},
    )
    assert response.status_code == 404
