"""Shared pytest fixtures for the test suite.

The most important fixture here is `stub_coach_anthropic`, which is autouse
and replaces `coach_service._call_anthropic` with a canned-JSON response for
every test. This prevents any test from accidentally making a live Anthropic
call when ANTHROPIC_API_KEY is present in the environment.

Individual tests in test_coach_service.py override this fixture via their own
monkeypatches.
"""

import json

import pytest

from app.core import coach_service
from app.core.rate_limit import reset_rate_limits


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
def stub_coach_anthropic(monkeypatch):
    """Replace coach_service._call_anthropic and _is_coach_eligible with stubs.

    - _call_anthropic returns a canned response so no live API calls are made.
    - _is_coach_eligible always returns True so eligibility gating doesn't
      silently swallow what individual tests are trying to verify.

    Tests that want to exercise parsing / fallback / eligibility logic should
    monkeypatch these functions themselves inside the test body (those override
    this fixture-level stub).
    """

    def _fake_call(system: str, user_message: str) -> str:
        # Return week-format or single-day format based on prompt shape
        if '"days"' in user_message or "days" in user_message.lower():
            return _CANNED_COACH_RESPONSE
        return _CANNED_SINGLE_COACH_RESPONSE

    monkeypatch.setattr(coach_service, "_call_anthropic", _fake_call)
    monkeypatch.setattr(coach_service, "_is_coach_eligible", lambda db, user_id: True)
    reset_rate_limits()
