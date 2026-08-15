"""Tests for the coach content generation service (coach_service.py).

`coach_service._call_anthropic` is monkeypatched in every test — these tests
never hit the real Anthropic API. The autouse stub in conftest.py covers any
incidental calls from other fixtures.
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

import app.core.workout_week_service as workout_week_service
from app.core import coach_service
from app.core.coach_service import CoachContent, WeekCoachContent, generate_coach_content
from app.core.database import SessionLocal
from app.main import app

client = TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _unique_email(prefix: str) -> str:
    return f"coach_{prefix}_{uuid.uuid4().hex[:8]}@example.com"


def _signup_and_get_token(email: str) -> tuple[str, str]:
    payload = {
        "email": email,
        "password": "StrongPass123",
        "preferred_name": "Coach",
        "last_name": "Tester",
    }
    resp = client.post("/v1/auth/signup", json=payload)
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    user_id = client.get("/v1/users/me", headers=_auth(token)).json()["id"]
    return token, user_id


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _set_onboarding(token: str, data: dict) -> None:
    resp = client.post("/v1/onboarding/me", json={"data": data, "is_complete": True}, headers=_auth(token))
    assert resp.status_code == 200


def _next_workout(token: str) -> dict:
    resp = client.post("/v1/workouts/next", headers=_auth(token))
    assert resp.status_code == 200
    return resp.json()


_EQUIPMENT_PAYLOAD = {
    "equipment_ids": ["dumbbells", "flat_bench", "olympic_barbell", "plates"],
}


# ── Fake exercise stub ────────────────────────────────────────────────────────


class _FakeExercise:
    def __init__(self, ex_id: str, name: str, why_it_works: str = ""):
        self.id = ex_id
        self.name = name
        self.why_it_works = why_it_works
        self.primary_muscle = "chest"


class _FakePrescription:
    def __init__(self, reason=None, weight=None):
        self.suggested_weight_reason = reason
        self.suggested_weight_kg = weight


# ── Unit: _parse_response ─────────────────────────────────────────────────────


def test_parse_valid_json_returns_coach_content():
    raw = json.dumps({
        "workoutIntent": "Build upper body strength today.",
        "exerciseRationale": {
            "bench_press": "Primary horizontal push; suggestion to increase load.",
            "push_up": "Bodyweight finisher.",
        },
    })
    result = coach_service._parse_response(raw, CoachContent("fallback", {}))
    assert result.workout_intent == "Build upper body strength today."
    assert result.exercise_rationale["bench_press"].startswith("Primary")
    assert "push_up" in result.exercise_rationale


def test_parse_strips_markdown_fences():
    raw = (
        "```json\n"
        '{"workoutIntent": "Great session.", "exerciseRationale": {}}'
        "\n```"
    )
    result = coach_service._parse_response(raw, CoachContent("fallback", {}))
    assert result.workout_intent == "Great session."


def test_parse_malformed_json_returns_fallback():
    fallback = CoachContent("My fallback.", {})
    result = coach_service._parse_response("not json at all", fallback)
    assert result is fallback


def test_parse_missing_intent_returns_fallback():
    raw = json.dumps({"exerciseRationale": {"push_up": "works chest"}})
    fallback = CoachContent("My fallback.", {})
    result = coach_service._parse_response(raw, fallback)
    assert result is fallback


def test_parse_empty_string_returns_fallback():
    fallback = CoachContent("My fallback.", {})
    result = coach_service._parse_response("", fallback)
    assert result is fallback


# ── Unit: _suggestion_line ────────────────────────────────────────────────────


def test_suggestion_line_increase():
    pres = _FakePrescription(reason="increase", weight=65.0)
    assert coach_service._suggestion_line(pres) == "increase to 65.0kg"


def test_suggestion_line_hold():
    pres = _FakePrescription(reason="hold", weight=60.0)
    assert coach_service._suggestion_line(pres) == "hold at 60.0kg"


def test_suggestion_line_deload():
    pres = _FakePrescription(reason="deload", weight=54.0)
    assert coach_service._suggestion_line(pres) == "deload to 54.0kg"


def test_suggestion_line_no_suggestion():
    pres = _FakePrescription()
    assert coach_service._suggestion_line(pres) == "no suggestion yet"


# ── Unit: generate_coach_content ─────────────────────────────────────────────


def test_generate_returns_fallback_when_no_user_id(monkeypatch):
    calls = []
    monkeypatch.setattr(coach_service, "_call_anthropic", lambda s, u: calls.append(1) or "")
    result = generate_coach_content(
        db=None,
        user_id=None,
        day_type="upper",
        title="Upper Body",
        exercise_items=[],
    )
    assert result.workout_intent == "Your Upper Body workout for today."
    assert calls == []


def test_generate_returns_fallback_when_no_db(monkeypatch):
    calls = []
    monkeypatch.setattr(coach_service, "_call_anthropic", lambda s, u: calls.append(1) or "")
    result = generate_coach_content(
        db=None,
        user_id="some-user-id",
        day_type="upper",
        title="Upper Body",
        exercise_items=[],
    )
    assert "Upper Body" in result.workout_intent
    assert calls == []


def test_generate_one_call_for_multiple_exercises(monkeypatch):
    """Only one _call_anthropic call per workout, regardless of exercise count."""
    calls = []

    def fake_call(system: str, user_message: str) -> str:
        calls.append({"system": system, "user_message": user_message})
        return json.dumps({
            "workoutIntent": "Work hard today.",
            "exerciseRationale": {
                "bench_press": "Main push movement.",
                "squat": "Leg driver.",
                "push_up": "Bodyweight burnout.",
                "lunge": "Unilateral work.",
            },
        })

    monkeypatch.setattr(coach_service, "_call_anthropic", fake_call)

    db = SessionLocal()
    try:
        exercises = [
            _FakeExercise("bench_press", "Bench Press", "Compound push."),
            _FakeExercise("squat", "Squat", "Compound lower."),
            _FakeExercise("push_up", "Push-Up"),
            _FakeExercise("lunge", "Lunge"),
        ]
        items = [(ex, _FakePrescription()) for ex in exercises]
        result = generate_coach_content(
            db=db,
            user_id="fake-user-id-not-in-db",
            day_type="full_body",
            title="Full Body",
            exercise_items=items,
        )
    finally:
        db.close()

    assert len(calls) == 1
    assert result.workout_intent == "Work hard today."
    assert "bench_press" in result.exercise_rationale


def test_generate_prompt_contains_fitness_goal_and_experience(monkeypatch):
    """System prompt must include fitnessGoal, experience level, and each exercise."""
    calls = []

    def fake_call(system: str, user_message: str) -> str:
        calls.append({"system": system, "user_message": user_message})
        return json.dumps({
            "workoutIntent": "Build strength.",
            "exerciseRationale": {},
        })

    monkeypatch.setattr(coach_service, "_call_anthropic", fake_call)

    token, user_id = _signup_and_get_token(_unique_email("prompt_content"))
    _set_onboarding(token, {
        **_EQUIPMENT_PAYLOAD,
        "experienceLevel": "intermediate",
        "fitnessGoal": "build_muscle",
    })

    db = SessionLocal()
    try:
        exercises = [_FakeExercise("push_up", "Push-Up", "Works chest and triceps.")]
        items = [(ex, _FakePrescription(reason="hold", weight=0.0)) for ex in exercises]
        generate_coach_content(
            db=db,
            user_id=user_id,
            day_type="upper",
            title="Upper Body",
            exercise_items=items,
        )
    finally:
        db.close()

    assert len(calls) == 1
    system = calls[0]["system"]
    assert "fitnessGoal" in system or "build_muscle" in system
    assert "intermediate" in system
    assert "push_up" in system or "Push-Up" in system


def test_generate_fallback_on_timeout(monkeypatch):
    """A timeout exception from _call_anthropic must yield template fallback, not raise."""
    import socket

    def fake_call(system: str, user_message: str) -> str:
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(coach_service, "_call_anthropic", fake_call)

    db = SessionLocal()
    try:
        result = generate_coach_content(
            db=db,
            user_id="fake-user",
            day_type="upper",
            title="Upper Body",
            exercise_items=[],
        )
    finally:
        db.close()

    assert "Upper Body" in result.workout_intent
    assert result.exercise_rationale == {}


def test_generate_fallback_on_bad_json(monkeypatch):
    """Unparseable LLM output must yield template fallback, not raise."""
    monkeypatch.setattr(
        coach_service, "_call_anthropic", lambda s, u: "this is not json"
    )

    db = SessionLocal()
    try:
        result = generate_coach_content(
            db=db,
            user_id="fake-user",
            day_type="lower",
            title="Lower Body",
            exercise_items=[],
        )
    finally:
        db.close()

    assert "Lower Body" in result.workout_intent
    assert result.exercise_rationale == {}


# ── Integration: /next exposes workoutIntent ──────────────────────────────────


def test_next_endpoint_includes_workout_intent(monkeypatch):
    """POST /next must return 200 and include workoutIntent in the response."""
    _INTENT = "Integration test intent."

    def fake_week_coach(db, *, user_id, days):
        return WeekCoachContent(days={
            d["workoutDayId"]: CoachContent(
                workout_intent=_INTENT,
                exercise_rationale={},
            )
            for d in days
        })

    monkeypatch.setattr(workout_week_service, "generate_week_coach_content", fake_week_coach)

    token, _ = _signup_and_get_token(_unique_email("next_intent"))
    _set_onboarding(token, _EQUIPMENT_PAYLOAD)

    data = _next_workout(token)
    assert "workoutIntent" in data
    assert data["workoutIntent"] == _INTENT


def test_next_endpoint_returns_200_when_llm_raises(monkeypatch):
    """If the LLM raises, /next must still return 200 with a template workoutIntent."""
    def fake_week_coach_raises(db, *, user_id, days):
        # Simulate what generate_week_coach_content does on LLM failure:
        # never raises, returns template fallback for every day.
        return WeekCoachContent(days={
            d["workoutDayId"]: CoachContent(
                workout_intent=f"Your {d['title']} workout for today.",
                exercise_rationale={},
            )
            for d in days
        })

    monkeypatch.setattr(workout_week_service, "generate_week_coach_content", fake_week_coach_raises)

    token, _ = _signup_and_get_token(_unique_email("next_llm_fail"))
    _set_onboarding(token, _EQUIPMENT_PAYLOAD)

    data = _next_workout(token)
    assert data["workoutIntent"] is not None
    assert len(data["workoutIntent"]) > 0


# ── Integration: /week exposes workoutIntent ──────────────────────────────────


def test_week_endpoint_includes_workout_intent(monkeypatch):
    """GET /week must return workoutIntent on each workout day."""
    _WEEK_INTENT = "Week test intent."

    def fake_week_coach(db, *, user_id, days):
        return WeekCoachContent(days={
            d["workoutDayId"]: CoachContent(
                workout_intent=_WEEK_INTENT,
                exercise_rationale={},
            )
            for d in days
        })

    monkeypatch.setattr(workout_week_service, "generate_week_coach_content", fake_week_coach)

    token, _ = _signup_and_get_token(_unique_email("week_intent"))
    _set_onboarding(token, _EQUIPMENT_PAYLOAD)

    resp = client.get("/v1/workouts/week?force=true", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()

    workouts = data["workouts"]
    assert len(workouts) > 0
    for w in workouts:
        assert "workoutIntent" in w
        assert w["workoutIntent"] == _WEEK_INTENT


# ── Integration: duration change avoids batched week coach call ────────────────


def test_duration_change_uses_single_day_coach_not_week_batch(monkeypatch):
    """Duration updates must not call generate_week_coach_content; only the changed day."""
    week_calls: list[int] = []
    day_calls: list[str] = []

    def track_week_coach(db, *, user_id, days):
        week_calls.append(len(days))
        return WeekCoachContent(days={
            d["workoutDayId"]: CoachContent(
                workout_intent="Week batch should not run.",
                exercise_rationale={},
            )
            for d in days
        })

    def track_day_coach(db, *, user_id, day_type, title, exercise_items):
        day_calls.append(day_type)
        return CoachContent(
            workout_intent=f"Single-day coach for {title}.",
            exercise_rationale={},
        )

    monkeypatch.setattr(workout_week_service, "generate_week_coach_content", track_week_coach)
    monkeypatch.setattr(workout_week_service, "generate_coach_content", track_day_coach)

    token, _ = _signup_and_get_token(_unique_email("duration_coach"))
    _set_onboarding(token, {**_EQUIPMENT_PAYLOAD, "days_per_week": 3})

    week_resp = client.get("/v1/workouts/week", headers=_auth(token))
    assert week_resp.status_code == 200
    initial = week_resp.json()
    changed = initial["workouts"][0]
    workout_id = changed["workoutDayId"]
    unchanged = initial["workouts"][1]
    unchanged_intent = unchanged.get("workoutIntent")

    week_calls.clear()
    day_calls.clear()

    duration_resp = client.patch(
        "/v1/workouts/week/duration",
        json={"workoutDayId": workout_id, "durationMinutes": 60},
        headers=_auth(token),
    )
    assert duration_resp.status_code == 200
    updated = duration_resp.json()

    assert week_calls == [], "duration change must not trigger batched week coach call"
    assert len(day_calls) == 1, "duration change should coach only the changed day"

    updated_changed = next(w for w in updated["workouts"] if w["workoutDayId"] == workout_id)
    updated_unchanged = next(w for w in updated["workouts"] if w["workoutDayId"] == unchanged["workoutDayId"])

    assert updated_changed["workoutIntent"] == f"Single-day coach for {updated_changed['title']}."
    if unchanged_intent:
        assert updated_unchanged["workoutIntent"] == unchanged_intent
