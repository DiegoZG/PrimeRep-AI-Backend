import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from scripts.equipment_scan_pilot import (
    EvaluationManifest, PILOT, ROOT, evaluate, main, readiness, review_manifest,
)


class Catalog:
    def __init__(self):
        self.options_calls = []

    def options(self, equipment, available, limit, cursor=None):
        self.options_calls.append((equipment, available, limit, cursor))
        exercise = dict((equipment, exercise) for equipment, exercise, _ in PILOT)[equipment]
        return SimpleNamespace(items=[SimpleNamespace(exercise_id=exercise)], next_cursor=None)

    def guidance(self, exercise):
        return SimpleNamespace(instructions=SimpleNamespace(status="published" if exercise == "lat_pulldown" else "instructions_unavailable"),
                               video=SimpleNamespace(status="video_unavailable"))


def test_review_manifest_reuses_only_existing_three_drafts():
    from app.schemas.exercise_catalog import CatalogManifest
    selected = review_manifest()
    CatalogManifest.model_validate(selected)
    assert [entry["id"] for entry in selected["exercises"]] == [row[1] for row in PILOT]
    for entry in selected["exercises"]:
        assert entry["generation_eligible"] is False
        assert "Unapproved" in entry["review_notes"]
        batch = next(row[2] for row in PILOT if row[1] == entry["id"])
        source = json.loads((ROOT / f"scripts/data/catalog/{batch}.json").read_text())
        assert entry == next(value for value in source["exercises"] if value["id"] == entry["id"])


def test_default_readiness_does_not_claim_database_or_photo_coverage(monkeypatch, capsys):
    def forbidden():
        raise AssertionError("Default report must not connect to a database")
    monkeypatch.setattr("scripts.equipment_scan_pilot.database_readiness", forbidden)
    monkeypatch.setattr("sys.argv", ["equipment_scan_pilot", "readiness"])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["catalog_status"] == "not_inspected"
    assert result["photo_accuracy"]["status"] == "not_assessed"
    assert all(row["linked"] is None for row in result["pilot"])


def test_readiness_uses_public_guidance_status_instead_of_draft_presence():
    catalog = Catalog()
    result = readiness(catalog)
    assert result["catalog_status"] == "blocked"
    assert result["pilot"][0]["missing"] == ["rights_cleared_approved_video"]
    assert result["pilot"][1]["missing"] == ["reviewed_published_instructions", "rights_cleared_approved_video"]
    assert result["photo_accuracy"]["status"] == "not_assessed"
    assert len(catalog.options_calls) == 3


def test_readiness_finds_expected_identity_after_an_options_page():
    catalog = Catalog()
    original = catalog.options
    def options(equipment, available, limit, cursor=None):
        if equipment == "lat_pulldown_cable" and cursor is None:
            return SimpleNamespace(items=[SimpleNamespace(exercise_id="other_variant")], next_cursor="next")
        return original(equipment, available, limit, cursor)
    catalog.options = options
    assert readiness(catalog)["pilot"][0]["linked"] is True
    assert catalog.options_calls[0][-1] == "next"


def test_readiness_distinguishes_missing_link_and_does_not_hide_storage_errors():
    catalog = Catalog()
    catalog.options = lambda *args, **kwargs: SimpleNamespace(items=[], next_cursor=None)
    assert readiness(catalog)["pilot"][0]["missing"] == ["equipment_exercise_link", "reviewed_published_instructions", "rights_cleared_approved_video"]
    def unavailable(*args, **kwargs):
        raise RuntimeError("storage unavailable")
    catalog.options = unavailable
    with pytest.raises(RuntimeError, match="storage unavailable"):
        readiness(catalog)


def case(identifier, expected, predicted, **extra):
    return {"case_id": identifier, "scenario": "Controlled test records, not actual photo evidence",
            "source_kind": "real_photo", "source_reference": "private://reviewed-test-record",
            "rights_status": "cleared", "permission_reference": "private://test-permission",
            "ground_truth_status": "reviewed", "reviewer": "test-reviewer",
            "expected_equipment_ids": expected, "predicted_equipment_ids": predicted, **extra}


def manifest(cases):
    return EvaluationManifest.model_validate({"schema_version": 1, "prediction_run_reference": "test-only-records",
        "model": "mock-model", "recognition_revision": "test-revision", "catalog_version": "test-catalog", "cases": cases})


def test_unsupplied_evaluation_template_reports_not_assessed():
    source = ROOT / "scripts/data/equipment-scan-pilot-evaluation.json"
    report = evaluate(EvaluationManifest.model_validate_json(source.read_text()))
    assert report["status"] == "not_assessed"
    assert report["assessed_real_photo_cases"] == 0
    assert report["exact_match_rate"] is None
    assert report["release_ready"] is False
    assert report["assessment_complete"] is False
    assert len(report["pending_case_ids"]) == 15


def test_evaluation_separates_wrong_matches_rejections_and_synthetic_checks():
    report = evaluate(manifest([
        case("correct", ["row_cable"], ["row_cable"]),
        case("wrong", ["lat_pulldown_cable"], ["leg_extension_machine"]),
        case("false-acceptance", [], ["leg_extension_machine"]),
        case("false-abstention", ["row_cable"], []),
        case("unreviewed", ["row_cable"], ["row_cable"], ground_truth_status="pending"),
        case("illustration", [], [], source_kind="illustration"),
    ]))
    assert report["assessed_real_photo_cases"] == 4
    assert report["exact_match_rate"] == 0.25
    assert report["wrong_machine_results"] == report["false_acceptances"] == report["false_abstentions"] == 1
    assert report["assessed_non_photo_checks"] == report["correct_non_photo_checks"] == 1
    assert report["pending_case_ids"] == ["unreviewed"]
    assert report["assessment_complete"] is False
    assert report["release_ready"] is False


@pytest.mark.parametrize("missing", ["permission_reference", "source_reference", "reviewer"])
def test_recorded_results_without_provenance_are_excluded(missing):
    report = evaluate(manifest([case("pending", ["row_cable"], ["row_cable"], **{missing: " "})]))
    assert report["status"] == "not_assessed"
    assert report["exact_match_rate"] is None


def test_synthetic_success_cannot_count_as_real_photo_accuracy():
    report = evaluate(manifest([case("drawing", [], [], source_kind="illustration")]))
    assert report["correct_non_photo_checks"] == 1
    assert report["status"] == "not_assessed"
    assert report["assessed_real_photo_cases"] == 0
    assert report["exact_match_rate"] is None


def test_extra_wrong_candidate_prevents_a_successful_match():
    report = evaluate(manifest([case("mixed", ["row_cable"], ["row_cable", "leg_extension_machine"])]))
    assert report["exact_matches"] == 0
    assert report["wrong_machine_results"] == 1
    assert report["exact_match_rate"] == 0


def test_recorded_predictions_require_run_metadata_and_unique_identities():
    record = case("one", ["row_cable"], ["row_cable"])
    with pytest.raises(ValidationError, match="Recorded predictions require"):
        EvaluationManifest.model_validate({"cases": [record]})
    with pytest.raises(ValidationError, match="Case identities must be unique"):
        manifest([record, record])
    with pytest.raises(ValidationError, match="Equipment identities must be unique"):
        manifest([case("one", ["row_cable"], ["row_cable", "row_cable"])])
    with pytest.raises(ValidationError, match="Illustrations require abstention"):
        manifest([case("one", ["row_cable"], ["row_cable"], source_kind="illustration")])


@pytest.mark.parametrize("identifier", ["", " ", "row cable"])
@pytest.mark.parametrize("field", ["expected_equipment_ids", "predicted_equipment_ids"])
def test_recorded_equipment_identities_must_be_nonempty_slugs(identifier, field):
    values = case("one", ["row_cable"], ["row_cable"])
    values[field] = [identifier]
    with pytest.raises(ValidationError):
        manifest([values])
