"""Reproducible seed/draft audit; optional database access is read-only and aggregate."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.catalog_build import build_manifest, inventory, seed_equipment, seed_exercises


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def audit() -> dict:
    manifest = build_manifest()
    result = inventory(manifest)
    seeds = seed_exercises()
    equipment = {entry["id"] for entry in seed_equipment()}
    original = json.loads((ROOT / "scripts/data/exercise_content.json").read_text())["exercises"]
    result["canonical_seed_ids"] = sorted(entry["id"] for entry in seeds)
    result["seed_equipment_count"] = len(equipment)
    result["seed_content_missing_ids"] = sorted({entry["id"] for entry in seeds} - {entry["id"] for entry in original})
    result["content_without_canonical_id"] = sorted({entry["id"] for entry in original} - {entry["id"] for entry in seeds})
    result["invalid_equipment_references"] = sorted({value for entry in manifest["exercises"] for value in entry["equipment_ids"]} - equipment)
    result["legacy_missing_text_fields"] = [{"id": entry["id"], "field": field} for entry in original for field in ("how_to", "why_it_works", "common_mistakes", "beginner_notes") if not entry.get(field, "").strip()]
    identities = {}
    for entry in manifest["exercises"]:
        for name in [entry["name"], *entry["aliases"]]:
            identities.setdefault(normalize(name), set()).add(entry["id"])
    result["name_alias_identity_collisions"] = {name: sorted(ids) for name, ids in identities.items() if len(ids) > 1}
    result["draft_media_count"] = 0
    result["seed_configured_media_urls"] = [{"id": entry["id"], "field": field, "url": entry[field]} for entry in seeds for field in ("demo_video_url", "image_url") if entry.get(field)]
    result["unresolved_equipment_requirements"] = {entry["id"]: "rated band anchor lacks catalog ID; generation disabled" for entry in manifest["exercises"] if "no current equipment ID" in entry["review_notes"]}
    result["identity_review_candidates"] = [
        {"ids": ["bodyweight_squat", "squat", "goblet_squat"], "rationale": "Different loading location and setup; existing barbell identity preserved."},
        {"ids": ["dumbbell_bench_press", "neutral_grip_dumbbell_press", "dumbbell_squeeze_press"], "rationale": "Grip/elbow path and intentional inward compression distinguish drafts; trainer must confirm useful separation."},
        {"ids": ["barbell_curl", "reverse_barbell_curl", "hammer_curl"], "rationale": "Supinated/pronated/neutral forearm positions change the task, not alternate names."},
        {"ids": ["single_leg_romanian_deadlift", "kickstand_romanian_deadlift"], "rationale": "Unsupported balance versus rear-toe support; trainer verifies distinct programming role."},
    ]
    result["excluded_identity_inflation"] = "Plate-loaded versus selectorized versions of otherwise identical chest press, hack squat and leg press were not counted as extra exercises. Media/model/angle variants never count as exercises."
    result["database"] = {"status": "not inspected; use --database for aggregate read-only inspection"}
    return result


def database_audit() -> dict:
    from sqlalchemy import text
    from app.core.database import engine
    canonical_ids = {row["id"] for row in seed_exercises()}
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            counts = connection.execute(text("SELECT source, owner_user_id IS NOT NULL AS owned, is_active, count(*) FROM exercises GROUP BY source, owned, is_active ORDER BY source, owned, is_active")).all()
            ids = set(connection.execute(text("SELECT id FROM exercises WHERE source='seed' AND owner_user_id IS NULL")).scalars())
            content = connection.execute(text("SELECT count(*) FILTER (WHERE how_to IS NULL OR how_to=''), count(*) FILTER (WHERE demo_video_url IS NOT NULL), count(*) FILTER (WHERE image_url IS NOT NULL) FROM exercises WHERE source='seed' AND owner_user_id IS NULL")).one()
            broken = connection.execute(text("SELECT count(*) FROM exercise_equipment ee LEFT JOIN equipment e ON e.id=ee.equipment_id WHERE e.id IS NULL")).scalar()
    return {"status": "read-only inspection completed", "counts": [{"source": row[0], "owned": row[1], "active": row[2], "count": row[3]} for row in counts],
            "canonical_seed_present": len(ids & canonical_ids), "canonical_seed_missing_ids": sorted(canonical_ids - ids),
            "noncanonical_seed_row_count": len(ids - canonical_ids), "noncanonical_note": "Excluded from 200 target; may include test fixtures or manually added legacy rows. No private exercise names or account IDs are output.",
            "seed_missing_how_to_count": content[0], "seed_video_url_count": content[1], "seed_image_url_count": content[2], "broken_equipment_reference_count": broken}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", action="store_true")
    args = parser.parse_args()
    result = audit()
    if args.database:
        try:
            result["database"] = database_audit()
        except Exception as exc:
            result["database"] = {"status": "inspection unavailable", "error_type": type(exc).__name__}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
