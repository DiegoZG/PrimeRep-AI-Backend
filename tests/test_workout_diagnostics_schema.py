from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.workout_diagnostics import WorkoutDiagnosticBatch


def event(**changes):
    return {"event_id": str(uuid4()), "event_type": "local_save", "occurred_at_ms": 1794081600000, "outcome": "success", "duration_ms": 3, "saved_count": 1, **changes}


def batch(events=None, **changes):
    return {"schema_version": 1, "platform": "ios", "app_version": "1.0.0", "events": [event()] if events is None else events, **changes}


def test_versioned_batch_preserves_uuid_and_omits_unknown_values():
    payload = batch()
    parsed = WorkoutDiagnosticBatch.model_validate(payload)
    encoded = parsed.model_dump(mode="json", exclude_none=True)
    assert encoded == payload
    assert "queue_age_ms" not in encoded["events"][0]


@pytest.mark.parametrize("extra", [{"user_id": "other"}, {"environment": "production"}, {"email": "private@example.com"}])
def test_batch_rejects_identity_and_unknown_properties(extra):
    with pytest.raises(ValidationError):
        WorkoutDiagnosticBatch.model_validate(batch(**extra))


@pytest.mark.parametrize("extra", [{"weight": 100}, {"reps": 8}, {"exercise_id": "bench"}, {"message": "private"}, {"payload": {"note": "private"}}])
def test_event_rejects_workout_values_and_freeform_properties(extra):
    with pytest.raises(ValidationError):
        WorkoutDiagnosticBatch.model_validate(batch([event(**extra)]))


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"schema_version": "1"}, {"schema_version": 2},
    {"platform": "other"}, {"app_version": "email@example.com"}, {"app_version": "x" * 65},
])
def test_strict_batch_fields(changes):
    with pytest.raises(ValidationError):
        WorkoutDiagnosticBatch.model_validate(batch(**changes))


@pytest.mark.parametrize("changes", [
    {"duration_ms": True}, {"duration_ms": "3"}, {"duration_ms": 1.5}, {"duration_ms": -1},
    {"duration_ms": 86400001}, {"queue_age_ms": 31536000001}, {"saved_count": 1000001},
    {"occurred_at_ms": True}, {"http_status": 99}, {"player_active": "true"},
    {"event_id": "invalid"}, {"error_category": "raw error"},
])
def test_strict_event_fields_and_bounds(changes):
    with pytest.raises(ValidationError):
        WorkoutDiagnosticBatch.model_validate(batch([event(**changes)]))


@pytest.mark.parametrize("events", [[], [event() for _ in range(21)]])
def test_batch_size_bounds(events):
    with pytest.raises(ValidationError):
        WorkoutDiagnosticBatch.model_validate(batch(events))


def test_operation_terminal_requires_trace_and_acknowledgment_kind():
    terminal = {"event_id": str(uuid4()), "event_type": "operation_terminal", "occurred_at_ms": 1794081600000, "trace_id": str(uuid4()), "operation_type": "set", "outcome": "acknowledged", "acknowledgment_kind": "reconciled"}
    assert WorkoutDiagnosticBatch.model_validate(batch([terminal])).events[0].outcome == "acknowledged"
    for missing in ("trace_id", "operation_type", "acknowledgment_kind"):
        with pytest.raises(ValidationError):
            WorkoutDiagnosticBatch.model_validate(batch([{key: value for key, value in terminal.items() if key != missing}]))
    with pytest.raises(ValidationError):
        WorkoutDiagnosticBatch.model_validate(batch([{**terminal, "outcome": "failure"}]))


def test_request_failure_is_not_a_terminal_outcome():
    failure = {"event_id": str(uuid4()), "event_type": "sync_result", "occurred_at_ms": 1794081600000, "trace_id": str(uuid4()), "operation_type": "set", "outcome": "failure", "error_category": "network"}
    assert WorkoutDiagnosticBatch.model_validate(batch([failure])).events[0].outcome == "failure"
    with pytest.raises(ValidationError):
        WorkoutDiagnosticBatch.model_validate(batch([{**failure, "acknowledgment_kind": "direct"}]))


def test_invalid_clocks_and_unknown_legacy_ages_are_not_zero():
    snapshot = {"event_id": str(uuid4()), "event_type": "queue_snapshot", "occurred_at_ms": 8640000000000000, "queue_depth": 2, "unknown_age_count": 2, "clock_invalid": True}
    parsed = WorkoutDiagnosticBatch.model_validate(batch([snapshot])).events[0]
    assert parsed.queue_age_ms is None
    with pytest.raises(ValidationError):
        WorkoutDiagnosticBatch.model_validate(batch([{**snapshot, "queue_age_ms": 0}]))
    with pytest.raises(ValidationError):
        WorkoutDiagnosticBatch.model_validate(batch([{**snapshot, "unknown_age_count": 3}]))
