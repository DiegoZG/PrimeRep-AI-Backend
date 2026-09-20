"""Compile the reviewed-by-humans-later authoring sources; never publish content."""
from __future__ import annotations

import argparse
import ast
import csv
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "scripts/data/catalog"
ACCESSED_AT = "2026-09-20"


def frozen_seed(filename: str, variable: str) -> list[dict]:
    tree = ast.parse((ROOT / "alembic/versions" / filename).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == variable for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(f"Frozen seed {variable} not found")


def seed_exercises() -> list[dict]:
    return frozen_seed("b7c8d9e0f1a2_add_exercises_tables.py", "SEED_EXERCISES")


def seed_equipment() -> list[dict]:
    return frozen_seed("a1b2c3d4e5f6_add_equipment_table.py", "EQUIPMENT_SEED")


LEGACY_FAMILIES = {
    "bench_press": "press", "incline_dumbbell_press": "press", "push_up": "press",
    "chest_fly_machine": "fly", "cable_crossover": "fly", "overhead_press": "shoulder_press",
    "dumbbell_shoulder_press": "shoulder_press", "lateral_raise": "raise", "front_raise": "raise",
    "reverse_fly": "rear", "deadlift": "hinge", "romanian_deadlift": "hinge", "pull_up": "pulldown",
    "lat_pulldown": "pulldown", "seated_row": "row", "t_bar_row": "row", "barbell_curl": "curl",
    "dumbbell_curl": "curl", "preacher_curl": "curl", "tricep_pushdown": "triceps",
    "skull_crushers": "triceps", "dip": "triceps", "squat": "squat", "front_squat": "squat",
    "leg_press": "squat", "leg_extension": "leg_extension", "leg_curl": "leg_curl",
    "walking_lunge": "lunge", "hip_thrust": "bridge", "glute_bridge": "bridge",
    "standing_calf_raise": "calf", "seated_calf_raise": "calf", "plank": "core",
    "crunch_machine": "core", "hanging_leg_raise": "core", "ab_wheel_rollout": "core",
    "rowing_machine": "cardio", "stationary_bike": "cardio", "treadmill_run": "cardio",
    "air_bike": "cardio", "ski_erg": "cardio",
}
ALIASES = {
    "bench_press": ["bench", "barbell bench press"], "romanian_deadlift": ["rdl", "barbell rdl"],
    "overhead_press": ["ohp", "standing barbell press"], "lat_pulldown": ["pulldown"],
    "squat": ["barbell back squat"], "dumbbell_shoulder_press": ["seated dumbbell press"],
    "reverse_fly": ["bent-over dumbbell reverse fly"], "leg_curl": ["lying leg curl"],
    "rear_foot_elevated_split_squat": ["bulgarian split squat"], "rear_delt_machine": ["rear delt fly machine"],
    "hip_thrust": ["barbell hip thrust"], "seated_row": ["cable row"],
}
SINGLE_DUMBBELL = {
    "single_arm_dumbbell_press", "single_arm_dumbbell_overhead_press", "single_arm_lateral_raise",
    "single_arm_dumbbell_row", "dumbbell_pullover", "concentration_curl", "dumbbell_preacher_curl",
    "dumbbell_overhead_extension", "single_arm_dumbbell_extension", "dumbbell_kickback", "goblet_squat",
    "goblet_box_squat", "dumbbell_lateral_lunge", "single_leg_romanian_deadlift", "dumbbell_glute_bridge",
    "dumbbell_hip_thrust",
    "heel_elevated_goblet_squat",
}
ALTERNATING = {"walking_lunge", "alternating_dumbbell_curl", "cross_body_hammer_curl", "reverse_lunge",
               "dumbbell_reverse_lunge", "forward_lunge", "dead_bug", "bird_dog", "bicycle_crunch", "plank_shoulder_tap"}
UNILATERAL = {"concentration_curl", "dumbbell_preacher_curl", "dumbbell_kickback", "cable_kickback",
              "cable_lateral_raise", "cable_front_raise", "bayesian_cable_curl", "half_kneeling_landmine_press",
              "standing_landmine_press", "kettlebell_overhead_press", "split_squat", "dumbbell_split_squat",
              "rear_foot_elevated_split_squat", "lateral_lunge", "dumbbell_lateral_lunge", "step_up",
              "lateral_step_up", "kickstand_romanian_deadlift", "standing_leg_curl", "cable_glute_kickback",
              "side_lying_hip_abduction", "side_plank", "kneeling_side_plank", "pallof_press",
              "band_pallof_press", "seated_cable_rotation"}


def modality(equipment: list[str]) -> str:
    if not equipment or set(equipment) <= {"pull_up_bar", "dip_bar", "flat_bench", "plyo_box", "trx", "stability_ball", "ab_wheel", "back_extension_bench", "glute_ham_raise_bench"}:
        return "bodyweight"
    if "loop_bands" in equipment:
        return "band"
    if "dumbbells" in equipment:
        return "dumbbell"
    if "kettlebells" in equipment:
        return "kettlebell"
    if any(e.endswith("_cable") for e in equipment):
        return "cable"
    if set(equipment) & {"olympic_barbell", "ez_bar", "short_bar", "trap_bar", "landmine"}:
        return "barbell"
    return "machine" if equipment != ["plates"] else "other"


def make_entry(row: dict, family: list, equipment: list[str], content: dict) -> dict:
    exercise_id = row["id"]
    mode = modality(equipment)
    basis = "per_implement" if mode in {"dumbbell", "kettlebell"} else "machine" if mode in {"machine", "cable"} else "total" if mode in {"barbell", "other"} else "bodyweight"
    if exercise_id.startswith("assisted_") or exercise_id == "band_assisted_pull_up":
        basis = "assistance"
    if exercise_id in {"dumbbell_glute_bridge", "barbell_glute_bridge", "dumbbell_hip_thrust", "hip_thrust"}:
        basis = "added"
    count = 2 if mode == "dumbbell" and exercise_id not in SINGLE_DUMBBELL else 1
    if basis != "per_implement":
        count = 1
    tracking = "duration" if family == FAMILIES["cardio"] or exercise_id in {"plank", "side_plank", "kneeling_side_plank"} else "reps"
    source = {"title": "ACE Exercise Library — " + family[4], "url": "https://www.acefitness.org/resources/everyone/exercise-library/" + (family[3] + "/" if family[3] else ""), "accessed_at": ACCESSED_AT, "claims": [family[5]]}
    if exercise_id in {"rowing_machine", "ski_erg"}:
        source = {"title": "Concept2 — " + ("Indoor Rowing Technique" if exercise_id == "rowing_machine" else "SkiErg Technique"), "url": "https://www.concept2.com/training/" + ("rowing-technique" if exercise_id == "rowing_machine" else "skierg-technique"), "accessed_at": ACCESSED_AT, "claims": ["Manufacturer guidance on drive/recovery sequence and machine posture."]}
    elif family == FAMILIES["cardio"]:
        source = {"title": "CDC — How to Measure Physical Activity Intensity", "url": "https://www.cdc.gov/physical-activity-basics/measuring/index.html", "accessed_at": ACCESSED_AT, "claims": ["Aerobic effort varies with the person and intensity; this source does not verify machine-specific setup."]}
    pattern = family[0]
    if exercise_id in {"front_raise", "plate_front_raise", "cable_front_raise", "floor_crunch", "reverse_crunch", "cable_crunch", "crunch_machine", "hanging_leg_raise", "hanging_knee_raise"}:
        pattern = "flexion"
    if exercise_id in {"bicycle_crunch", "seated_cable_rotation"}:
        pattern = "rotation"
    if exercise_id == "dip":
        pattern = "vertical_push"
    return {
        "id": exercise_id, "name": row["name"], "exercise_type": row.get("exercise_type", "bodyweight" if mode == "bodyweight" else "accessory" if family[0] in {"flexion", "extension", "abduction", "adduction", "plantar_flexion", "anti_rotation", "isolation"} else "strength"),
        "primary_muscle": row.get("primary_muscle", family[1]), "secondary_muscles": row.get("secondary_muscles", family[2]),
        "equipment_ids": equipment, "aliases": ALIASES.get(exercise_id, []), "movement_pattern": pattern,
        "resistance_modality": mode, "difficulty": "advanced" if exercise_id in {"pendlay_row", "glute_ham_raise", "single_leg_box_squat"} else "intermediate" if mode == "barbell" or exercise_id.startswith("single_leg") else "beginner",
        "laterality": "alternating" if exercise_id in ALTERNATING else "unilateral" if exercise_id.startswith("single_") or exercise_id in UNILATERAL else "bilateral",
        "load_profile": {"basis": basis, "implement_count": count}, "tracking_mode": tracking,
        "structured_content": content, "generation_eligible": False, "sources": [source],
        "review_notes": "Unapproved original editorial draft. Source claim is limited to the cited movement-family fact; it does not establish all variant details. Trainer must verify setup, equipment attachments, range, muscle classification and load interpretation. Generation remains disabled until explicitly reviewed. " + ("Legacy identity and original instruction text retained for comparison; provenance of older wording is unknown. " if exercise_id in LEGACY_FAMILIES else "") + ("A rated band anchor is required but has no current equipment ID: resolve equipment taxonomy before approving this draft. " if exercise_id in {"band_chest_press", "band_standing_row", "band_pallof_press"} else "") + ("Timed tracking is reference-only in this phase." if tracking != "reps" else ""),
    }


FAMILIES = json.loads((DATA / "families.json").read_text())


def build_manifest() -> dict:
    from app.schemas.exercise_catalog import CatalogManifest
    original = {e["id"]: e for e in json.loads((ROOT / "scripts/data/exercise_content.json").read_text())["exercises"]}
    entries = []
    for row in seed_exercises():
        family = FAMILIES[LEGACY_FAMILIES[row["id"]]]
        old = original[row["id"]]
        content = {"steps": re.split(r"(?<=[.!?])\s+", old["how_to"]), "cues": [family[6]],
                   "mistakes": re.split(r"(?<=[.!?])\s+", old["common_mistakes"]),
                   "benefits": family[6], "beginner_guidance": family[7]}
        entries.append(make_entry(row, family, row["required_equipment_ids"], content))
    with (DATA / "new-drafts.psv").open(newline="") as handle:
        for row in csv.DictReader(handle, delimiter="|"):
            family = FAMILIES[row["family"]]
            content = {"steps": [row["setup"], row["action"]], "cues": [row["cue"]], "mistakes": [row["mistake"]], "benefits": family[6], "beginner_guidance": family[7]}
            entries.append(make_entry(row, family, row["equipment"].split(",") if row["equipment"] else [], content))
    result = CatalogManifest.model_validate({"schema_version": 1, "exercises": entries}).model_dump(mode="json")
    known_equipment = {e["id"] for e in seed_equipment()}
    missing = {e for row in entries for e in row["equipment_ids"]} - known_equipment
    if missing:
        raise ValueError(f"Unknown equipment IDs: {sorted(missing)}")
    return result


def inventory(manifest: dict) -> dict:
    rows = manifest["exercises"]
    return {"target": 200, "draft_count": len(rows), "canonical_seed_count": len(seed_exercises()),
            "legacy_content_count": len(LEGACY_FAMILIES), "new_draft_count": len(rows) - len(LEGACY_FAMILIES),
            "batch_size": 25, "batches": (len(rows) + 24) // 25, "human_approvals": 0, "published_new_records": 0,
            "movement_patterns": dict(sorted(Counter(r["movement_pattern"] for r in rows).items())),
            "modalities": dict(sorted(Counter(r["resistance_modality"] for r in rows).items())),
            "unresolved": ["Trainer review for every draft", "Publication review for every draft", "Variant-specific technique references beyond movement-family references", "Production media assets and rights"]}


def review_packet(manifest: dict) -> str:
    sections = []
    for entry in manifest["exercises"]:
        content = entry["structured_content"]
        sections.append(f'<article id="{html.escape(entry["id"])}"><h2>{html.escape(entry["name"])}</h2><p><code>{entry["id"]}</code> · Draft · No approvals</p>')
        sections.append('<p>' + html.escape(entry["review_notes"]) + '</p>')
        sections.append('<dl>' + ''.join(f'<dt>{html.escape(key)}</dt><dd>{html.escape(str(entry[key]))}</dd>' for key in ('movement_pattern', 'primary_muscle', 'secondary_muscles', 'equipment_ids', 'resistance_modality', 'load_profile', 'tracking_mode', 'laterality', 'difficulty', 'aliases', 'generation_eligible')) + '</dl>')
        for field in ('steps', 'cues', 'mistakes'):
            sections.append(f'<h3>{field.title()}</h3><ol>' + ''.join(f'<li>{html.escape(value)}</li>' for value in content[field]) + '</ol>')
        sections.append('<h3>Benefits</h3><p>' + html.escape(content['benefits']) + '</p><h3>Beginner guidance</h3><p>' + html.escape(content['beginner_guidance']) + '</p>')
        sections.append('<h3>Research references — not media licenses</h3><ul>' + ''.join(f'<li><a href="{html.escape(s["url"], quote=True)}">{html.escape(s["title"])}</a> · accessed {s["accessed_at"]}<p>{html.escape(" ".join(s["claims"]))}</p></li>' for s in entry['sources']) + '</ul><p>Media: pending original assets and independent approvals.</p></article>')
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>PrimeRep draft review packet</title><style>body{max-width:900px;margin:32px auto;padding:0 20px;font:16px/1.6 system-ui;color:#20242a}article{border-top:2px solid #ddd;margin-top:40px;padding-top:20px}dt{font-weight:bold}dd{margin-bottom:8px}h3{margin-bottom:0}a{color:#065d9a}</style><body><h1>PrimeRep exercise drafts</h1><p>Editorial drafts for trainer review. No publication or technique approval is implied. Review commands bind to imported revision hashes; this packet cannot approve content.</p>' + ''.join(sections) + '</body></html>'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, choices=range(1, 9))
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--packet", action="store_true", help="Print a readable HTML packet, optionally limited by --batch")
    args = parser.parse_args()
    manifest = build_manifest()
    if args.inventory:
        output = inventory(manifest)
    elif args.batch:
        output = {"schema_version": 1, "exercises": manifest["exercises"][(args.batch - 1) * 25:args.batch * 25]}
    else:
        output = manifest
    print(review_packet(output) if args.packet else json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
