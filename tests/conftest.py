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


_CANNED_COACH_RESPONSE = json.dumps(
    {
        "workoutIntent": "Stub workout intent from conftest.",
        "exerciseRationale": {},
    }
)


@pytest.fixture(autouse=True)
def stub_coach_anthropic(monkeypatch):
    """Replace coach_service._call_anthropic with a deterministic stub.

    Tests that want to exercise the real parsing / fallback logic should
    monkeypatch coach_service._call_anthropic themselves *after* this fixture
    runs (fixture-level monkeypatches take precedence in the same scope, but
    per-test monkeypatches applied inside the test body override this one).
    """

    def _fake_call(system: str, user_message: str) -> str:
        return _CANNED_COACH_RESPONSE

    monkeypatch.setattr(coach_service, "_call_anthropic", _fake_call)
