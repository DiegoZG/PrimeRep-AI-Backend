import json
import copy
from datetime import date
from pathlib import Path

import pytest

from scripts.catalog_build import ALIASES, DATA, build_manifest, editorial_audit, fingerprint, review_packet, seed_equipment, seed_exercises
from app.schemas.exercise_catalog import CatalogEntry, CatalogManifest


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
            assert source["claims"] and date.fromisoformat(source["accessed_at"]) <= date.today()


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


def test_cues_are_exercise_specific_and_cannot_duplicate_benefits():
    manifest = build_manifest()
    report = editorial_audit(manifest)
    assert report["cue_benefit_duplicates"] == []
    assert report["shared_cue_sets"] == []
    assert report["ambiguous_source_titles"] == []
    assert report["generation_enabled"] == []
    assert report["human_approvals_recorded_by_builder"] == 0
    entry = copy.deepcopy(manifest["exercises"][0])
    entry["structured_content"]["cues"] = ["  " + entry["structured_content"]["benefits"].upper() + "  "]
    with pytest.raises(ValueError, match="must not duplicate"):
        CatalogEntry.model_validate(entry)


def test_corrected_packet_identifies_exact_content_and_escaped_diff():
    from app.core.exercise_catalog_service import content_hash

    manifest = build_manifest()
    for entry in manifest["exercises"]:
        assert fingerprint(entry) == content_hash(CatalogEntry.model_validate(entry).model_dump(mode="json"))
    manifest["exercises"] = manifest["exercises"][:1]
    previous = copy.deepcopy(manifest)
    previous["exercises"][0]["structured_content"]["cues"] = ["<script>previous draft</script>"]
    packet = review_packet(manifest, previous)
    assert fingerprint(manifest) in packet
    assert fingerprint(manifest["exercises"][0]) in packet
    assert fingerprint(previous) in packet
    assert "Changes from previous draft" in packet
    assert "&lt;script&gt;previous draft&lt;/script&gt;" in packet
    assert "<script>" not in packet
    changed = copy.deepcopy(manifest)
    changed["exercises"][0]["structured_content"]["cues"][0] += " Changed."
    assert fingerprint(changed) != fingerprint(manifest)


def test_review_findings_are_drafts_not_approval_or_runtime_migrations():
    entries = {entry["id"]: entry for entry in build_manifest()["exercises"]}
    assert entries["deadlift"]["primary_muscle"] == "glutes"
    assert entries["dip"]["structured_content"]["cues"] != entries["tricep_pushdown"]["structured_content"]["cues"]
    assert "palms facing forward" in " ".join(entries["dumbbell_curl"]["structured_content"]["steps"])
    assert "one or both" not in " ".join(entries["front_raise"]["structured_content"]["steps"])
    for exercise_id in ("bench_press", "squat", "skull_crushers", "deadlift", "pull_up", "dip", "cable_crossover", "t_bar_row"):
        assert "BLOCKING:" in entries[exercise_id]["review_notes"]
        assert entries[exercise_id]["generation_eligible"] is False
    for exercise_id in ("dumbbell_shoulder_press", "romanian_deadlift", "lat_pulldown", "tricep_pushdown"):
        assert "AI editorial review is not approval" in entries[exercise_id]["review_notes"]
