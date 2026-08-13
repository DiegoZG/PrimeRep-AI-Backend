"""
Tests for app.core.progression_service.suggest_weight_kg.

These use the real dev DB (same convention as the rest of the suite). Each
test signs up a unique user so history cannot leak between cases.

Note: These tests assume migrations have been run and seed data exists.
"""
import datetime
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.progression_service import (
    LOWER_INCREMENT_KG,
    UPPER_INCREMENT_KG,
    suggest_weight_kg,
)
from app.main import app

client = TestClient(app)

BENCH = "bench_press"  # chest → upper increment
SQUAT = "squat"  # quads → lower increment
MAIN_REPS_MIN = 6
MAIN_REPS_MAX = 10


def _unique_email(prefix: str) -> str:
    return f"prog_{prefix}_{uuid.uuid4().hex[:8]}@example.com"


def _signup(prefix: str) -> tuple[str, str]:
    email = _unique_email(prefix)
    resp = client.post(
        "/v1/auth/signup",
        json={
            "email": email,
            "password": "StrongPass123",
            "preferred_name": "Test",
            "last_name": "User",
        },
    )
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    me = client.get("/v1/users/me", headers=_auth(token))
    assert me.status_code == 200
    return token, me.json()["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _complete_session(
    token: str,
    *,
    exercise_id: str,
    sets: list[tuple[int, float]],
    workout_date: datetime.date,
    day_type: str = "upper",
) -> None:
    payload = {
        "workoutDayId": f"day-{uuid.uuid4().hex[:8]}",
        "workoutDate": workout_date.isoformat(),
        "dayType": day_type,
    }
    session = client.post("/v1/workouts/sessions", json=payload, headers=_auth(token))
    assert session.status_code == 201
    session_id = session.json()["id"]
    for index, (reps, weight) in enumerate(sets, start=1):
        resp = client.post(
            f"/v1/workouts/sessions/{session_id}/sets",
            json={
                "exerciseId": exercise_id,
                "setNumber": index,
                "reps": reps,
                "weightKg": weight,
            },
            headers=_auth(token),
        )
        assert resp.status_code == 201
    complete = client.patch(
        f"/v1/workouts/sessions/{session_id}/complete", headers=_auth(token)
    )
    assert complete.status_code == 200


def _suggest(user_id: str, exercise_id: str, is_lower_body: bool):
    db = SessionLocal()
    try:
        return suggest_weight_kg(
            db,
            user_id=user_id,
            exercise_id=exercise_id,
            is_lower_body=is_lower_body,
            reps_min=MAIN_REPS_MIN,
            reps_max=MAIN_REPS_MAX,
        )
    finally:
        db.close()


def test_no_history_returns_none():
    _, user_id = _signup("none")
    assert _suggest(user_id, BENCH, is_lower_body=False) is None


def test_top_of_range_increases_upper_by_2_5kg():
    token, user_id = _signup("upper")
    _complete_session(
        token,
        exercise_id=BENCH,
        sets=[(10, 60.0), (10, 60.0), (10, 60.0), (10, 60.0)],
        workout_date=datetime.date.today(),
    )
    suggestion = _suggest(user_id, BENCH, is_lower_body=False)
    assert suggestion is not None
    assert suggestion.reason == "increase"
    assert suggestion.weight_kg == 60.0 + UPPER_INCREMENT_KG


def test_top_of_range_increases_lower_by_5kg():
    token, user_id = _signup("lower")
    _complete_session(
        token,
        exercise_id=SQUAT,
        sets=[(10, 80.0), (10, 80.0), (10, 80.0), (10, 80.0)],
        workout_date=datetime.date.today(),
        day_type="lower",
    )
    suggestion = _suggest(user_id, SQUAT, is_lower_body=True)
    assert suggestion is not None
    assert suggestion.reason == "increase"
    assert suggestion.weight_kg == 80.0 + LOWER_INCREMENT_KG


def test_missed_bottom_once_holds_weight():
    token, user_id = _signup("hold")
    _complete_session(
        token,
        exercise_id=BENCH,
        sets=[(10, 60.0), (8, 60.0), (5, 60.0), (6, 60.0)],
        workout_date=datetime.date.today(),
    )
    suggestion = _suggest(user_id, BENCH, is_lower_body=False)
    assert suggestion is not None
    assert suggestion.reason == "hold"
    assert suggestion.weight_kg == 60.0


def test_missed_bottom_twice_consecutive_deloads_10_percent():
    token, user_id = _signup("deload")
    today = datetime.date.today()
    _complete_session(
        token,
        exercise_id=BENCH,
        sets=[(5, 60.0), (5, 60.0), (4, 60.0), (5, 60.0)],
        workout_date=today - timedelta(days=3),
    )
    _complete_session(
        token,
        exercise_id=BENCH,
        sets=[(5, 60.0), (4, 60.0), (5, 60.0), (5, 60.0)],
        workout_date=today,
    )
    suggestion = _suggest(user_id, BENCH, is_lower_body=False)
    assert suggestion is not None
    assert suggestion.reason == "deload"
    assert suggestion.weight_kg == 54.0


def test_in_range_but_not_top_holds():
    token, user_id = _signup("mid")
    _complete_session(
        token,
        exercise_id=BENCH,
        sets=[(8, 60.0), (8, 60.0), (7, 60.0), (8, 60.0)],
        workout_date=datetime.date.today(),
    )
    suggestion = _suggest(user_id, BENCH, is_lower_body=False)
    assert suggestion is not None
    assert suggestion.reason == "hold"
    assert suggestion.weight_kg == 60.0


def test_bodyweight_session_returns_none():
    token, user_id = _signup("bw")
    payload = {
        "workoutDayId": f"day-{uuid.uuid4().hex[:8]}",
        "workoutDate": datetime.date.today().isoformat(),
        "dayType": "upper",
    }
    session = client.post("/v1/workouts/sessions", json=payload, headers=_auth(token))
    session_id = session.json()["id"]
    client.post(
        f"/v1/workouts/sessions/{session_id}/sets",
        json={"exerciseId": "push_up", "setNumber": 1, "reps": 15},
        headers=_auth(token),
    )
    client.patch(f"/v1/workouts/sessions/{session_id}/complete", headers=_auth(token))
    assert _suggest(user_id, "push_up", is_lower_body=False) is None


def test_another_users_history_does_not_affect_suggestion():
    token_a, user_id_a = _signup("iso_a")
    token_b, user_id_b = _signup("iso_b")
    _complete_session(
        token_a,
        exercise_id=BENCH,
        sets=[(10, 100.0), (10, 100.0), (10, 100.0), (10, 100.0)],
        workout_date=datetime.date.today(),
    )
    assert _suggest(user_id_b, BENCH, is_lower_body=False) is None
    suggestion_a = _suggest(user_id_a, BENCH, is_lower_body=False)
    assert suggestion_a is not None
    assert suggestion_a.weight_kg == 100.0 + UPPER_INCREMENT_KG
