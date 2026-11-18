"""Read-only pilot readiness and offline assessment of recorded recognition results."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
PILOT = (
    ("lat_pulldown_cable", "lat_pulldown", "batch-01"),
    ("row_cable", "seated_row", "batch-01"),
    ("leg_extension_machine", "leg_extension", "batch-02"),
)
EquipmentId = Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_-]*$")]


def review_manifest():
    entries = {}
    for batch in {row[2] for row in PILOT}:
        path = ROOT / "scripts/data/catalog" / f"{batch}.json"
        entries.update({entry["id"]: entry for entry in json.loads(path.read_text())["exercises"]})
    return {"schema_version": 1, "exercises": [entries[exercise] for _, exercise, _ in PILOT]}


def readiness(catalog=None):
    rows = []
    for equipment, exercise, batch in PILOT:
        row = {"equipment_id": equipment, "exercise_id": exercise,
               "draft": f"scripts/data/catalog/{batch}.json",
               "review_packet": f"docs/catalog-review/{batch}.html#{exercise}",
               "linked": None, "instructions_published": None, "video_approved": None}
        if catalog is not None:
            try:
                page = catalog.options(equipment, [equipment], limit=20)
                linked = any(item.exercise_id == exercise for item in page.items)
                while not linked and page.next_cursor:
                    page = catalog.options(equipment, [equipment], limit=20, cursor=page.next_cursor)
                    linked = any(item.exercise_id == exercise for item in page.items)
                row.update(linked=linked, instructions_published=False, video_approved=False)
                if linked:
                    guide = catalog.guidance(exercise)
                    row.update(instructions_published=guide.instructions.status == "published",
                               video_approved=guide.video.status == "approved")
            except Exception as exc:
                if getattr(exc, "code", None) != "scan_not_found":
                    raise
                row.update(linked=False, instructions_published=False, video_approved=False)
            row["missing"] = [name for name, present in (
                ("equipment_exercise_link", row["linked"]),
                ("reviewed_published_instructions", row["instructions_published"]),
                ("rights_cleared_approved_video", row["video_approved"])) if not present]
        rows.append(row)
    return {"schema_version": 1, "catalog_status": "not_inspected" if catalog is None else
            "ready" if all(not row["missing"] for row in rows) else "blocked",
            "photo_accuracy": {"status": "not_assessed", "reason": "No real-photo predictions assessed by this report."},
            "pilot": rows}


def database_readiness():
    from sqlalchemy import text
    from app.core.database import SessionLocal
    from app.core.equipment_scan_catalog import SQLCatalogRepository

    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        return readiness(SQLCatalogRepository(db))


class PhotoCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=100)
    scenario: str = Field(min_length=1, max_length=200)
    source_kind: Literal["real_photo", "illustration"]
    source_reference: Optional[str] = None
    rights_status: Literal["pending", "cleared"] = "pending"
    permission_reference: Optional[str] = None
    ground_truth_status: Literal["pending", "reviewed"] = "pending"
    reviewer: Optional[str] = None
    expected_equipment_ids: list[EquipmentId] = Field(max_length=5)
    predicted_equipment_ids: Optional[list[EquipmentId]] = Field(default=None, max_length=5)

    @model_validator(mode="after")
    def unique_equipment(self):
        if self.source_kind == "illustration" and self.expected_equipment_ids:
            raise ValueError("Illustrations require abstention, not equipment matches")
        for values in (self.expected_equipment_ids, self.predicted_equipment_ids):
            if values is not None and len(values) != len(set(values)):
                raise ValueError("Equipment identities must be unique per case")
        return self


class EvaluationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    prediction_run_reference: Optional[str] = None
    model: Optional[str] = None
    recognition_revision: Optional[str] = None
    catalog_version: Optional[str] = None
    cases: list[PhotoCase] = Field(max_length=500)

    @model_validator(mode="after")
    def recorded_results(self):
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("Case identities must be unique")
        if any(case.predicted_equipment_ids is not None for case in self.cases):
            if not all(value and value.strip() for value in (
                self.prediction_run_reference, self.model, self.recognition_revision, self.catalog_version)):
                raise ValueError("Recorded predictions require run, model, recognition and catalog references")
        return self


def evaluate(manifest: EvaluationManifest):
    assessed, pending, non_photo = [], [], []
    for case in manifest.cases:
        eligible = (case.predicted_equipment_ids is not None and case.rights_status == "cleared"
                    and case.ground_truth_status == "reviewed" and all(value and value.strip() for value in
                    (case.source_reference, case.permission_reference, case.reviewer)))
        if eligible and case.source_kind == "real_photo":
            assessed.append(case)
        elif eligible:
            non_photo.append(case)
        else:
            pending.append(case.case_id)
    correct = sum(set(case.expected_equipment_ids) == set(case.predicted_equipment_ids) for case in assessed)
    return {"schema_version": 1, "status": "recorded_predictions_assessed" if assessed else "not_assessed",
            "assessment_complete": bool(assessed) and not pending,
            "real_photo_cases": sum(case.source_kind == "real_photo" for case in manifest.cases),
            "assessed_real_photo_cases": len(assessed), "exact_matches": correct,
            "exact_match_rate": correct / len(assessed) if assessed else None,
            "false_acceptances": sum(not case.expected_equipment_ids and bool(case.predicted_equipment_ids) for case in assessed),
            "wrong_machine_results": sum(bool(case.expected_equipment_ids) and bool(case.predicted_equipment_ids)
                                         and set(case.expected_equipment_ids) != set(case.predicted_equipment_ids) for case in assessed),
            "false_abstentions": sum(bool(case.expected_equipment_ids) and not case.predicted_equipment_ids for case in assessed),
            "assessed_non_photo_checks": len(non_photo),
            "correct_non_photo_checks": sum(set(case.expected_equipment_ids) == set(case.predicted_equipment_ids) for case in non_photo),
            "pending_case_ids": pending,
            "release_ready": False,
            "limitations": "Offline assessment of supplied records only; does not run recognition, verify photo provenance or approve release."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("readiness")
    report.add_argument("--database", action="store_true")
    sub.add_parser("review-manifest")
    score = sub.add_parser("evaluate")
    score.add_argument("manifest", type=Path)
    args = parser.parse_args()
    if args.command == "readiness":
        result = database_readiness() if args.database else readiness()
    elif args.command == "review-manifest":
        result = review_manifest()
    else:
        result = evaluate(EvaluationManifest.model_validate_json(args.manifest.read_text()))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
