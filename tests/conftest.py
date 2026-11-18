"""Shared pytest fixtures for the test suite.

The most important fixture here is `stub_coach_ai`, which is autouse
and replaces `coach_service._call_ai` with a canned-JSON response for
every test. This prevents any test from accidentally making a live OpenAI
call when OPENAI_API_KEY is present in the environment.

Individual tests in test_coach_service.py override this fixture via their own
monkeypatches.
"""

import json

import pytest

from app.core import coach_service
from app.core.rate_limit import reset_rate_limits


LEGAL_ACCEPTANCE = {
    "accepted": True,
    "termsVersion": "2026-09-12",
    "privacyVersion": "2026-10-02",
}


_CANNED_COACH_RESPONSE = json.dumps(
    {
        "days": [
            {
                "workoutDayId": "__stub__",
                "workoutIntent": "Stub workout intent from conftest.",
                "exerciseRationale": {},
            }
        ]
    }
)

_CANNED_SINGLE_COACH_RESPONSE = json.dumps(
    {
        "workoutIntent": "Stub workout intent from conftest.",
        "exerciseRationale": {},
    }
)


@pytest.fixture(autouse=True)
def stub_coach_ai(monkeypatch):
    """Stub provider calls while retaining real eligibility checks."""

    def _fake_call(system: str, user_message: str) -> str:
        # Return week-format or single-day format based on prompt shape
        if '"days"' in user_message or "days" in user_message.lower():
            return _CANNED_COACH_RESPONSE
        return _CANNED_SINGLE_COACH_RESPONSE

    monkeypatch.setattr(coach_service, "_call_ai", _fake_call)
    reset_rate_limits()


@pytest.fixture(autouse=True)
def block_live_openai_inference(monkeypatch):
    import openai

    def blocked(*args, **kwargs):
        raise AssertionError("Live OpenAI inference is not allowed in automated tests")

    monkeypatch.setattr(openai, "OpenAI", blocked)
    monkeypatch.setattr(openai, "AsyncOpenAI", blocked)
