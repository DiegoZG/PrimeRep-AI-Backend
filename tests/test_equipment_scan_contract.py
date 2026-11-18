import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.equipment_scan import (
    EquipmentSearchPage, ExerciseGuidance, ExerciseOptionsPage, ScanAttemptStatus,
    ScanErrorResponse, ScanResult,
)

FIXTURES = Path(__file__).parent / "fixtures" / "equipment_scanning"


@pytest.mark.parametrize("name,model", [
    ("scan_candidates", ScanResult), ("scan_no_match", ScanResult),
    ("equipment_search", EquipmentSearchPage), ("exercise_options", ExerciseOptionsPage),
    ("guidance_unavailable", ExerciseGuidance), ("error", ScanErrorResponse), ("access_unknown", ScanErrorResponse),
    ("attempt_processing", ScanAttemptStatus), ("attempt_completed", ScanAttemptStatus),
    ("attempt_unknown", ScanAttemptStatus),
])
def test_shared_v1_contract_fixtures(name, model):
    payload = json.loads((FIXTURES / (name + ".json")).read_text())
    assert model.model_validate(payload).model_dump(mode="json") == payload


def test_no_match_cannot_smuggle_candidates_or_provider_payload():
    payload = json.loads((FIXTURES / "scan_no_match.json").read_text())
    payload["candidates"] = json.loads((FIXTURES / "scan_candidates.json").read_text())["candidates"]
    with pytest.raises(ValidationError):
        ScanResult.model_validate(payload)
    payload["candidates"] = []
    payload["provider_response"] = "private"
    with pytest.raises(ValidationError):
        ScanResult.model_validate(payload)


def test_unavailable_guidance_never_contains_legacy_content_or_video():
    payload = json.loads((FIXTURES / "guidance_unavailable.json").read_text())
    payload["video"]["url"] = "https://legacy.example/video.mp4"
    with pytest.raises(ValidationError):
        ExerciseGuidance.model_validate(payload)


def test_completed_recovery_requires_the_same_normalized_result_contract():
    payload = json.loads((FIXTURES / "attempt_completed.json").read_text())
    payload["result"] = None
    with pytest.raises(ValidationError):
        ScanAttemptStatus.model_validate(payload)
