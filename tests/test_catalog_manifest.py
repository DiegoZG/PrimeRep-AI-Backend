import json
from pathlib import Path

from scripts.catalog_build import ALIASES, DATA, build_manifest, review_packet, seed_equipment, seed_exercises
from app.schemas.exercise_catalog import CatalogManifest


def test_review_batches_are_reproducible_and_cover_canonical_identity():
    expected = build_manifest()
    batches = [CatalogManifest.model_validate_json(path.read_text()) for path in sorted(DATA.glob("batch-*.json"))]
    assert len(batches) == 8
    assert all(len(batch.exercises) == 25 for batch in batches)
    entries = [entry.model_dump(mode="json") for batch in batches for entry in batch.exercises]
    assert entries == expected["exercises"]
    assert len({entry["id"] for entry in entries}) == 200
    assert {entry["id"] for entry in seed_exercises()} <= {entry["id"] for entry in entries}
    by_id = {entry["id"]: entry for entry in entries}
    for exercise_id, aliases in ALIASES.items():
        assert by_id[exercise_id]["aliases"] == aliases


def test_drafts_have_no_approvals_resolve_equipment_and_do_not_inflate_aliases():
    entries = build_manifest()["exercises"]
    equipment = {e["id"] for e in seed_equipment()}
    names = {e["name"].casefold(): e["id"] for e in entries}
    for entry in entries:
        assert set(entry["equipment_ids"]) <= equipment
        assert entry["generation_eligible"] is False
        assert "Unapproved" in entry["review_notes"]
        assert len(entry["structured_content"]["steps"]) >= 2
        for alias in entry["aliases"]:
            assert alias.casefold() not in names or names[alias.casefold()] == entry["id"]
        for source in entry["sources"]:
            assert source["claims"] and source["accessed_at"] == "2026-09-20"


def test_single_implement_and_unsupported_tracking_are_explicit():
    entries = {e["id"]: e for e in build_manifest()["exercises"]}
    for exercise_id in ("goblet_squat", "dumbbell_overhead_extension", "single_arm_dumbbell_row", "heel_elevated_goblet_squat"):
        assert entries[exercise_id]["load_profile"]["implement_count"] == 1
    assert entries["dumbbell_shoulder_press"]["load_profile"]["implement_count"] == 2
    assert entries["plank"]["tracking_mode"] == "duration"
    assert entries["rowing_machine"]["tracking_mode"] == "duration"
    assert "no current equipment ID" in entries["band_chest_press"]["review_notes"]


def test_html_review_packet_escapes_authored_text_and_preserves_sources():
    manifest = build_manifest()
    manifest["exercises"] = manifest["exercises"][:1]
    manifest["exercises"][0]["name"] = "<script>alert('x')</script>"
    packet = review_packet(manifest)
    assert "<script>" not in packet
    assert "&lt;script&gt;" in packet
    assert "https://www.acefitness.org/" in packet
    assert "No approvals" in packet
