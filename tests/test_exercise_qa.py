"""
Tests for the exercise AI Q&A feature:
- POST /v1/exercises/{exercise_id}/ask
- GET /v1/exercises/{exercise_id}/questions

`_call_anthropic` is monkeypatched in every test — these tests never hit the
real Anthropic API.

Note: These tests assume migrations have been run (including
f2a3b4c5d6e7_add_exercise_content_and_questions) and seed data exists in the
dev DB.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core import exercise_qa_service
from app.core.database import SessionLocal
from app.main import app
from app.models.exercise import Exercise
from app.models.exercise_question import ExerciseQuestion

client = TestClient(app)


def _unique_email(prefix: str) -> str:
    return f"exqa_{prefix}_{uuid.uuid4().hex[:8]}@example.com"


def _signup_and_get_token(email: str) -> tuple[str, str]:
    payload = {
        "email": email,
        "password": "StrongPass123",
        "preferred_name": "Test",
        "last_name": "User",
    }
    resp = client.post("/v1/auth/signup", json=payload)
    assert resp.status_code == 201
    token = resp.json()["access_token"]

    me_resp = client.get("/v1/users/me", headers=_auth(token))
    assert me_resp.status_code == 200
    return token, me_resp.json()["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _set_experience_level(token: str, level: str) -> None:
    resp = client.post(
        "/v1/onboarding/me",
        json={"data": {"experienceLevel": level}},
        headers=_auth(token),
    )
    assert resp.status_code == 200


def _set_exercise_field(exercise_id: str, field: str, value) -> None:
    db = SessionLocal()
    try:
        exercise = db.query(Exercise).filter(Exercise.id == exercise_id).first()
        setattr(exercise, field, value)
        db.commit()
    finally:
        db.close()


def _insert_question(user_id: str, exercise_id: str, created_at=None) -> None:
    db = SessionLocal()
    try:
        qa = ExerciseQuestion(
            id=str(uuid.uuid4()),
            user_id=user_id,
            exercise_id=exercise_id,
            question="filler question",
            answer="filler answer",
        )
        db.add(qa)
        db.commit()
        if created_at is not None:
            db.execute(
                ExerciseQuestion.__table__.update()
                .where(ExerciseQuestion.id == qa.id)
                .values(created_at=created_at)
            )
            db.commit()
    finally:
        db.close()


@pytest.fixture
def mock_anthropic_success(monkeypatch):
    """Patch _call_anthropic to succeed and capture the (system, user_message) args."""
    calls = []

    def fake_call(system: str, user_message: str) -> str:
        calls.append({"system": system, "user_message": user_message})
        return "Here is a helpful, mocked coaching answer."

    monkeypatch.setattr(exercise_qa_service, "_call_anthropic", fake_call)
    return calls


@pytest.fixture
def mock_anthropic_failure(monkeypatch):
    def fake_call(system: str, user_message: str) -> str:
        raise RuntimeError("simulated Anthropic outage")

    monkeypatch.setattr(exercise_qa_service, "_call_anthropic", fake_call)


# ── Auth guards ───────────────────────────────────────────────────────────────

def test_ask_requires_auth():
    resp = client.post("/v1/exercises/push_up/ask", json={"question": "How do I do this?"})
    assert resp.status_code == 401


def test_questions_requires_auth():
    resp = client.get("/v1/exercises/push_up/questions")
    assert resp.status_code == 401


# ── 404 for unknown exercise ─────────────────────────────────────────────────

def test_ask_unknown_exercise_returns_404(mock_anthropic_success):
    token, _ = _signup_and_get_token(_unique_email("ask_unknown"))
    resp = client.post(
        "/v1/exercises/not-a-real-exercise/ask",
        json={"question": "How do I do this?"},
        headers=_auth(token),
    )
    assert resp.status_code == 404


def test_questions_unknown_exercise_returns_404():
    token, _ = _signup_and_get_token(_unique_email("questions_unknown"))
    resp = client.get(
        "/v1/exercises/not-a-real-exercise/questions",
        headers=_auth(token),
    )
    assert resp.status_code == 404


# ── Successful ask + history ─────────────────────────────────────────────────

def test_ask_returns_answer_and_persists(mock_anthropic_success):
    token, _ = _signup_and_get_token(_unique_email("ask_success"))
    resp = client.post(
        "/v1/exercises/push_up/ask",
        json={"question": "What muscles does this work?"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "Here is a helpful, mocked coaching answer."
    assert body["question"] == "What muscles does this work?"
    assert body["exerciseId"] == "push_up"
    assert "id" in body
    assert "createdAt" in body

    history_resp = client.get("/v1/exercises/push_up/questions", headers=_auth(token))
    assert history_resp.status_code == 200
    items = history_resp.json()["items"]
    assert len(items) == 1
    assert items[0]["question"] == "What muscles does this work?"
    assert items[0]["answer"] == "Here is a helpful, mocked coaching answer."


def test_questions_history_newest_first(mock_anthropic_success):
    token, _ = _signup_and_get_token(_unique_email("history_order"))

    for question in ["first question", "second question", "third question"]:
        resp = client.post(
            "/v1/exercises/push_up/ask",
            json={"question": question},
            headers=_auth(token),
        )
        assert resp.status_code == 200

    resp = client.get("/v1/exercises/push_up/questions", headers=_auth(token))
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 3
    assert items[0]["question"] == "third question"
    assert items[-1]["question"] == "first question"


def test_questions_cross_user_isolation(mock_anthropic_success):
    token_a, _ = _signup_and_get_token(_unique_email("qa_owner"))
    token_b, _ = _signup_and_get_token(_unique_email("qa_intruder"))

    resp = client.post(
        "/v1/exercises/push_up/ask",
        json={"question": "owner's question"},
        headers=_auth(token_a),
    )
    assert resp.status_code == 200

    resp = client.get("/v1/exercises/push_up/questions", headers=_auth(token_b))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ── Request validation ────────────────────────────────────────────────────────

def test_ask_rejects_empty_question(mock_anthropic_success):
    token, _ = _signup_and_get_token(_unique_email("ask_empty"))
    resp = client.post(
        "/v1/exercises/push_up/ask",
        json={"question": ""},
        headers=_auth(token),
    )
    assert resp.status_code == 422


def test_ask_rejects_too_long_question(mock_anthropic_success):
    token, _ = _signup_and_get_token(_unique_email("ask_too_long"))
    resp = client.post(
        "/v1/exercises/push_up/ask",
        json={"question": "x" * 501},
        headers=_auth(token),
    )
    assert resp.status_code == 422


# ── Prompt / context building ────────────────────────────────────────────────

def test_ask_system_prompt_includes_exercise_content_and_experience(mock_anthropic_success):
    token, _ = _signup_and_get_token(_unique_email("prompt_context"))
    _set_experience_level(token, "intermediate")
    _set_exercise_field("push_up", "how_to", "TEST_HOW_TO_MARKER")

    resp = client.post(
        "/v1/exercises/push_up/ask",
        json={"question": "Is my form okay?"},
        headers=_auth(token),
    )
    assert resp.status_code == 200

    assert len(mock_anthropic_success) == 1
    system_prompt = mock_anthropic_success[0]["system"]
    assert "TEST_HOW_TO_MARKER" in system_prompt
    assert "intermediate" in system_prompt
    assert "push_up" in system_prompt or "Push-Up" in system_prompt

    user_message = mock_anthropic_success[0]["user_message"]
    assert user_message == "Is my form okay?"


# ── Rate limiting ─────────────────────────────────────────────────────────────

def test_ask_rate_limited_after_daily_limit(mock_anthropic_success):
    token, user_id = _signup_and_get_token(_unique_email("rate_limit"))

    now = datetime.now(timezone.utc)
    for _ in range(exercise_qa_service.DAILY_QUESTION_LIMIT):
        _insert_question(user_id, "push_up", created_at=now)

    resp = client.post(
        "/v1/exercises/push_up/ask",
        json={"question": "One more question please"},
        headers=_auth(token),
    )
    assert resp.status_code == 429
    assert "20" in resp.json()["detail"]

    # Sanity check: the LLM should never have been called once rate-limited.
    assert len(mock_anthropic_success) == 0


def test_ask_not_rate_limited_by_yesterdays_questions(mock_anthropic_success):
    token, user_id = _signup_and_get_token(_unique_email("rate_limit_reset"))

    yesterday = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
    for _ in range(exercise_qa_service.DAILY_QUESTION_LIMIT):
        _insert_question(user_id, "push_up", created_at=yesterday)

    resp = client.post(
        "/v1/exercises/push_up/ask",
        json={"question": "Fresh question today"},
        headers=_auth(token),
    )
    assert resp.status_code == 200


# ── LLM failure ───────────────────────────────────────────────────────────────

def test_ask_returns_503_on_anthropic_failure(mock_anthropic_failure):
    token, _ = _signup_and_get_token(_unique_email("llm_failure"))
    resp = client.post(
        "/v1/exercises/push_up/ask",
        json={"question": "Will this fail gracefully?"},
        headers=_auth(token),
    )
    assert resp.status_code == 503

    # Failed attempts should not be persisted.
    history_resp = client.get("/v1/exercises/push_up/questions", headers=_auth(token))
    assert history_resp.status_code == 200
    assert history_resp.json()["items"] == []
