"""Session-owned exercise replacement and ordering, serialized with set writes."""
import copy
import hashlib
import json
from typing import Optional

from sqlalchemy.orm import Session

from app.core.exercise_service import exercise_to_dict
from app.core.workout_logging_service import InvalidOperationReuse
from app.core.workout_service import (
    ACCESSORY_PRESCRIPTION, MAIN_EXERCISE_TYPES, MAIN_PRESCRIPTION, SPLIT_CYCLES,
    _exercise_to_schema,
)
from app.core.workout_template_service import list_substitutions, TemplateNotFoundError
from app.core.workout_week_service import _prescription_with_suggestion
from app.models.exercise import Exercise
from app.models.set_log import SetLog
from app.models.workout_session import WorkoutSession
from app.models.workout_session_operation import WorkoutSessionOperation
from app.schemas.workout import WorkoutPrescriptionOut


class ArrangementConflict(Exception):
    pass


class InvalidArrangement(Exception):
    pass


def _session(db: Session, session_id: str, user_id: str) -> Optional[WorkoutSession]:
    return (db.query(WorkoutSession)
            .filter_by(id=session_id, user_id=user_id)
            .with_for_update().populate_existing().first())


def _require_editable(session: WorkoutSession, expected_revision: Optional[int] = None) -> None:
    if session.status != "in_progress":
        raise ArrangementConflict("Only an in-progress workout can be rearranged.")
    if session.effective_workout is None:
        raise ArrangementConflict("Recover this workout before rearranging it.")
    if expected_revision is not None and session.arrangement_revision != expected_revision:
        raise ArrangementConflict("The workout changed. Review your order against the latest workout.")


def _items(workout: dict) -> list[tuple[str, dict]]:
    return [(block["blockType"], item)
            for block in workout.get("exerciseBlocks", []) for item in block.get("items", [])]


def _item_id(item: dict) -> str:
    return item["exercise"]["id"]


def _find_item(session: WorkoutSession, exercise_id: str) -> dict:
    for _, item in _items(session.effective_workout or {}):
        if _item_id(item) == exercise_id:
            return item
    raise InvalidArrangement("Exercise is not part of this workout.")


def _started_ids(db: Session, session_id: str) -> set[str]:
    # Deleted sets remain history and must also protect an exercise's identity.
    return {row[0] for row in db.query(SetLog.exercise_id).filter_by(session_id=session_id).all()}


def _require_unstarted(db: Session, session_id: str, exercise_id: str) -> None:
    if exercise_id in _started_ids(db, session_id):
        raise ArrangementConflict("An exercise with a logged set cannot be replaced, even after deleting the set.")


def _candidates(db: Session, session: WorkoutSession, user_id: str, exercise_id: str) -> list[Exercise]:
    _find_item(session, exercise_id)
    present = {_item_id(item) for _, item in _items(session.effective_workout)}
    try:
        candidates = list_substitutions(db, user_id, exercise_id, owned_only=True)
    except TemplateNotFoundError:
        raise InvalidArrangement("This exercise no longer has available replacements.")
    return [exercise for exercise in candidates if exercise.id not in present]


def replacement_candidates(db: Session, session_id: str, user_id: str, exercise_id: str):
    session = _session(db, session_id, user_id)
    if session is None:
        return None
    _require_editable(session)
    _find_item(session, exercise_id)
    _require_unstarted(db, session_id, exercise_id)
    return {"items": [exercise_to_dict(exercise, user_id=user_id)
                      for exercise in _candidates(db, session, user_id, exercise_id)]}


def _fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _operation(db: Session, session_id: str, operation_id: str, kind: str, payload: dict) -> bool:
    existing = db.query(WorkoutSessionOperation).filter_by(
        session_id=session_id, client_operation_id=operation_id).first()
    if existing is not None:
        if existing.operation_type != kind or existing.payload_fingerprint != _fingerprint(payload):
            raise InvalidOperationReuse
        return True
    if db.query(SetLog.id).filter_by(session_id=session_id, client_operation_id=operation_id).first():
        raise InvalidOperationReuse
    return False


def _save(db: Session, session: WorkoutSession, workout: dict, operation_id: str, kind: str, payload: dict):
    session.arranged_workout = workout
    session.arrangement_revision += 1
    db.add(WorkoutSessionOperation(session_id=session.id, client_operation_id=operation_id,
                                  operation_type=kind, payload_fingerprint=_fingerprint(payload)))
    db.commit()
    db.refresh(session)
    return session


def replace_exercise(db: Session, session_id: str, user_id: str, exercise_id: str,
                     replacement_exercise_id: str, expected_revision: int, client_operation_id: str):
    session = _session(db, session_id, user_id)
    if session is None:
        return None
    payload = {"exerciseId": exercise_id, "replacementExerciseId": replacement_exercise_id,
               "expectedRevision": expected_revision}
    if _operation(db, session_id, client_operation_id, "replace_exercise", payload):
        return session
    _require_editable(session, expected_revision)
    _require_unstarted(db, session_id, exercise_id)
    candidates = _candidates(db, session, user_id, exercise_id)
    replacement = next((exercise for exercise in candidates if exercise.id == replacement_exercise_id), None)
    if replacement is None:
        raise InvalidArrangement("Choose an available, compatible replacement using your equipment.")
    workout = copy.deepcopy(session.effective_workout)
    item = next(item for _, item in _items(workout) if _item_id(item) == exercise_id)
    prescription = WorkoutPrescriptionOut.model_validate(item["prescription"]).model_copy(update={
        "suggested_weight_kg": None, "suggested_previous_weight_kg": None, "suggested_weight_reason": None})
    item["exercise"] = _exercise_to_schema(replacement).model_dump(by_alias=True)
    item["prescription"] = _prescription_with_suggestion(
        db, user_id=user_id, exercise=replacement, base=prescription).model_dump(by_alias=True)
    item["exerciseRationale"] = None
    return _save(db, session, workout, client_operation_id, "replace_exercise", payload)


def _validate_order(workout: dict, exercise_ids: list[str]) -> dict[str, tuple[str, dict]]:
    rows = _items(workout)
    current = {_item_id(item): (role, item) for role, item in rows}
    if (len(current) != len(rows) or len(exercise_ids) != len(current)
            or len(set(exercise_ids)) != len(exercise_ids) or set(exercise_ids) != set(current)):
        raise InvalidArrangement("Provide every workout exercise exactly once.")
    return current


def _ordered_workout(workout: dict, exercise_ids: list[str], rows: dict) -> dict:
    result = copy.deepcopy(workout)
    blocks = []
    for exercise_id in exercise_ids:
        role, item = rows[exercise_id]
        if not blocks or blocks[-1]["blockType"] != role:
            blocks.append({"blockType": role, "items": []})
        blocks[-1]["items"].append(copy.deepcopy(item))
    result["exerciseBlocks"] = blocks
    return result


def _numeric_targets(prescription: dict) -> tuple:
    return tuple(prescription.get(key) for key in ("sets", "repsMin", "repsMax", "restSeconds"))


def _preview(db: Session, session: WorkoutSession, user_id: str, exercise_ids: list[str]) -> dict:
    workout = session.effective_workout
    rows = copy.deepcopy(_validate_order(workout, exercise_ids))
    changes = []
    if (workout.get("splitKey") in {*SPLIT_CYCLES, "ai-optimized", "custom"}
            and not (workout.get("programActivationId") or workout.get("templateId") or workout.get("splitKey") == "program")):
        original = session.workout_snapshot or workout
        main_count = sum(role == "main" for role, _ in _items(original))
        started = _started_ids(db, session.id)
        catalog = {item.id: item for item in db.query(Exercise).filter(Exercise.id.in_(exercise_ids)).all()}
        bases = {"main": MAIN_PRESCRIPTION, "accessory": ACCESSORY_PRESCRIPTION}

        def untouched(exercise_id: str) -> bool:
            role, item = rows[exercise_id]
            exercise = catalog.get(exercise_id)
            return bool(role in bases and exercise_id not in started and exercise and exercise.is_active
                        and exercise.generation_eligible and exercise.exercise_type in MAIN_EXERCISE_TYPES | {"accessory"}
                        and _numeric_targets(item["prescription"]) == _numeric_targets(bases[role].model_dump(by_alias=True)))

        promote = [exercise_id for exercise_id in exercise_ids[:main_count]
                   if rows[exercise_id][0] == "accessory" and untouched(exercise_id)
                   and catalog[exercise_id].exercise_type in MAIN_EXERCISE_TYPES]
        demote = [exercise_id for exercise_id in exercise_ids[main_count:]
                  if rows[exercise_id][0] == "main" and untouched(exercise_id)]
        for promoted, demoted in zip(promote, demote):
            for exercise_id, new_role in ((promoted, "main"), (demoted, "accessory")):
                old_role, item = rows[exercise_id]
                before = copy.deepcopy(item["prescription"])
                after = _prescription_with_suggestion(db, user_id=user_id, exercise=catalog[exercise_id],
                                                     base=bases[new_role]).model_dump(by_alias=True)
                item["prescription"] = after
                item["exerciseRationale"] = None
                rows[exercise_id] = (new_role, item)
                changes.append({"exerciseId": exercise_id, "fromBlockType": old_role,
                                "toBlockType": new_role, "before": before, "after": after})
    return {"arrangementRevision": session.arrangement_revision, "exerciseIds": exercise_ids,
            "targetsChanged": bool(changes), "targetChanges": changes,
            "effectiveWorkout": _ordered_workout(workout, exercise_ids, rows),
            "message": ("Review the paired main and accessory target changes." if changes
                        else "Order updated; targets unchanged.")}


def preview_order(db: Session, session_id: str, user_id: str, exercise_ids: list[str], expected_revision: int):
    session = _session(db, session_id, user_id)
    if session is None:
        return None
    _require_editable(session, expected_revision)
    return _preview(db, session, user_id, exercise_ids)


def update_order(db: Session, session_id: str, user_id: str, exercise_ids: list[str], expected_revision: int,
                 client_operation_id: str, apply_revised_targets: bool = False):
    session = _session(db, session_id, user_id)
    if session is None:
        return None
    payload = {"exerciseIds": exercise_ids, "expectedRevision": expected_revision,
               "applyRevisedTargets": apply_revised_targets}
    if _operation(db, session_id, client_operation_id, "reorder_exercises", payload):
        return session
    _require_editable(session, expected_revision)
    rows = _validate_order(session.effective_workout, exercise_ids)
    workout = (_preview(db, session, user_id, exercise_ids)["effectiveWorkout"] if apply_revised_targets else
               _ordered_workout(session.effective_workout, exercise_ids, rows))
    return _save(db, session, workout, client_operation_id, "reorder_exercises", payload)
