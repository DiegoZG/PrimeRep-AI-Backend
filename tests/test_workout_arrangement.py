"""Session-only arrangement, compatibility, history and conflict regression tests."""
import copy
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.workout_service import MAIN_PRESCRIPTION, ACCESSORY_PRESCRIPTION
from app.main import app
from app.models.exercise import Exercise
from app.models.equipment import Equipment
from app.models.workout_session import WorkoutSession
from conftest import LEGAL_ACCEPTANCE
from workout_test_utils import install_startable_workout

client = TestClient(app)


def _token():
    response = client.post("/v1/auth/signup", json={
        "email": f"arrangement_{uuid.uuid4().hex}@example.com", "password": "StrongPass123",
        "preferred_name": "Arrangement", "last_name": "Test", "legalAcceptance": LEGAL_ACCEPTANCE})
    assert response.status_code == 201
    return response.json()["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def workout():
    token = _token()
    headers = _headers(token)
    user_id = client.get("/v1/users/me", headers=headers).json()["id"]
    ids = [f"arrangement_{uuid.uuid4().hex}" for _ in range(4)]
    with SessionLocal() as db:
        for exercise_id in ids:
            db.add(Exercise(id=exercise_id, name=exercise_id, exercise_type="strength",
                            primary_muscle="chest", secondary_muscles=[], source="user",
                            owner_user_id=user_id, is_active=True, generation_eligible=True))
        db.commit()
    day = install_startable_workout(client, token, exercise_ids=tuple(ids[:3]))
    response = client.post("/v1/workouts/sessions", headers=headers, json={
        "workoutDayId": day["workoutDayId"], "workoutDate": day["date"],
        "dayType": day["dayType"], "clientSessionId": str(uuid.uuid4())})
    assert response.status_code == 201
    session = response.json()
    return {"token": token, "headers": headers, "user": user_id, "ids": ids,
            "session": session, "url": f"/v1/workouts/sessions/{session['id']}"}


def _order(workout, ids=None, **overrides):
    return {"exerciseIds": ids or list(reversed(workout["ids"][:3])), "expectedRevision": 0,
            "clientOperationId": str(uuid.uuid4()), **overrides}


def _replace(workout, original=None, replacement=None, **overrides):
    return client.post(f"{workout['url']}/exercises/{original or workout['ids'][0]}/replace",
                       headers=workout["headers"], json={
                           "replacementExerciseId": replacement or workout["ids"][3],
                           "expectedRevision": 0, "clientOperationId": str(uuid.uuid4()), **overrides})


def _flat(snapshot):
    return [item for block in snapshot["exerciseBlocks"] for item in block["items"]]


def _log(workout, exercise_id=None):
    response = client.post(workout["url"] + "/sets", headers=workout["headers"], json={
        "exerciseId": exercise_id or workout["ids"][0], "setNumber": 1, "reps": 8,
        "weightKg": 20, "clientOperationId": str(uuid.uuid4())})
    assert response.status_code == 201
    return response.json()


def _canonical_roles(workout, *, program=False, unknown=False):
    with SessionLocal() as db:
        session = db.get(WorkoutSession, workout["session"]["id"])
        snapshot = copy.deepcopy(session.workout_snapshot)
        items = _flat(snapshot)
        for index, item in enumerate(items):
            item["prescription"] = (MAIN_PRESCRIPTION if index < 2 else ACCESSORY_PRESCRIPTION).model_dump(by_alias=True)
            item["exerciseRationale"] = "Original explanation"
        snapshot["exerciseBlocks"] = [{"blockType": "main", "items": items[:2]},
                                      {"blockType": "accessory", "items": items[2:]}]
        snapshot["splitKey"] = "unknown" if unknown else "upper_lower"
        if program:
            snapshot["templateId"] = "authored-template"
            snapshot["programActivationId"] = "program-activation"
        session.workout_snapshot = copy.deepcopy(snapshot)
        session.arranged_workout = snapshot
        db.commit()
    return snapshot


def test_session_effective_workout_starts_as_independent_original_and_legacy_falls_back(workout):
    assert workout["session"]["effectiveWorkout"] == workout["session"]["workoutSnapshot"]
    assert workout["session"]["arrangementRevision"] == 0
    with SessionLocal() as db:
        session = db.get(WorkoutSession, workout["session"]["id"])
        session.arranged_workout = None
        db.commit()
    response = client.get(workout["url"], headers=workout["headers"]).json()
    assert response["effectiveWorkout"] == response["workoutSnapshot"]
    assert _replace(workout).status_code == 200


def test_replacement_preserves_original_targets_clears_rationale_and_uses_effective_membership(workout):
    snapshot = _canonical_roles(workout)
    response = _replace(workout)
    assert response.status_code == 200
    session = response.json()
    assert session["arrangementRevision"] == 1
    assert session["workoutSnapshot"] == snapshot
    item = _flat(session["effectiveWorkout"])[0]
    assert item["exercise"]["id"] == workout["ids"][3]
    assert item["prescription"] == _flat(snapshot)[0]["prescription"]
    assert item["exerciseRationale"] is None
    _log(workout, workout["ids"][3])
    old = client.post(workout["url"] + "/sets", headers=workout["headers"], json={
        "exerciseId": workout["ids"][0], "setNumber": 1, "reps": 8})
    assert old.status_code == 422


def test_candidates_are_owned_compatible_equipped_visible_and_exclude_workout(workout):
    extra = {}
    with SessionLocal() as db:
        equipment = db.query(Equipment).first()
        for label, attrs in {
            "inactive": {"is_active": False}, "private": {"owner_user_id": str(uuid.uuid4())},
            "incompatible": {"primary_muscle": "back"},
            "reviewed": {"content_version": "test", "tracking_mode": "duration"},
            "equipment": {}, "draft": {"source": "catalog", "owner_user_id": None, "publication_status": "draft"},
        }.items():
            exercise_id = f"arrangement_{uuid.uuid4().hex}"
            # A real other owner is necessary because custom exercises have a user FK.
            if label == "private":
                other_token = _token()
                attrs["owner_user_id"] = client.get("/v1/users/me", headers=_headers(other_token)).json()["id"]
            values = {"id": exercise_id, "name": label, "exercise_type": "strength", "primary_muscle": "chest",
                      "secondary_muscles": [], "source": "user", "owner_user_id": workout["user"], "is_active": True}
            values.update(attrs)
            exercise = Exercise(**values)
            if label == "equipment":
                exercise.equipment = [equipment]
            db.add(exercise)
            extra[label] = exercise_id
        db.commit()
    response = client.get(f"{workout['url']}/exercises/{workout['ids'][0]}/replacement-candidates", headers=workout["headers"])
    assert response.status_code == 200
    candidate_ids = {item["id"] for item in response.json()["items"]}
    assert workout["ids"][3] in candidate_ids
    assert not candidate_ids.intersection(workout["ids"][:3])
    assert not candidate_ids.intersection(extra.values())
    for exercise_id in extra.values():
        assert _replace(workout, replacement=exercise_id).status_code == 422


def test_replacement_blocked_after_set_even_when_deleted(workout):
    logged = _log(workout)
    assert _replace(workout).status_code == 409
    deleted = client.delete(f"{workout['url']}/sets/{logged['id']}", headers=workout["headers"],
                            params={"clientOperationId": str(uuid.uuid4())})
    assert deleted.status_code == 204
    assert _replace(workout).status_code == 409
    assert client.get(f"{workout['url']}/exercises/{workout['ids'][0]}/replacement-candidates", headers=workout["headers"]).status_code == 409


def test_replacement_idempotency_and_payload_revision_validation(workout):
    operation_id = str(uuid.uuid4())
    first = _replace(workout, clientOperationId=operation_id)
    again = _replace(workout, clientOperationId=operation_id)
    assert first.status_code == again.status_code == 200
    assert first.json()["arrangementRevision"] == again.json()["arrangementRevision"] == 1
    assert _replace(workout, clientOperationId=operation_id, replacement=workout["ids"][1]).status_code == 409
    assert _replace(workout, original=workout["ids"][1]).status_code == 409


def test_reorder_full_permutation_idempotency_and_snapshot_preservation(workout):
    logged = _log(workout)
    payload = _order(workout)
    response = client.patch(workout["url"] + "/order", headers=workout["headers"], json=payload)
    assert response.status_code == 200
    session = response.json()
    assert [item["exercise"]["id"] for item in _flat(session["effectiveWorkout"])] == payload["exerciseIds"]
    assert session["workoutSnapshot"] == workout["session"]["workoutSnapshot"]
    assert session["setLogs"] == [logged]
    assert client.patch(workout["url"] + "/order", headers=workout["headers"], json=payload).json()["arrangementRevision"] == 1
    changed = {**payload, "exerciseIds": workout["ids"][:3]}
    assert client.patch(workout["url"] + "/order", headers=workout["headers"], json=changed).status_code == 409
    changed["clientOperationId"] = str(uuid.uuid4())
    assert client.patch(workout["url"] + "/order", headers=workout["headers"], json=changed).status_code == 409


@pytest.mark.parametrize("indexes", [[0, 0, 1], [0, 1], [0, 1, 3]])
def test_order_rejects_non_permutations(workout, indexes):
    ids = [workout["ids"][index] for index in indexes]
    assert client.patch(workout["url"] + "/order", headers=workout["headers"], json=_order(workout, ids)).status_code == 422


def test_preview_pairs_generated_roles_and_apply_is_opt_in(workout):
    snapshot = _canonical_roles(workout)
    payload = _order(workout)
    preview = client.post(workout["url"] + "/order/preview", headers=workout["headers"], json=payload)
    assert preview.status_code == 200
    assert preview.json()["targetsChanged"]
    assert len(preview.json()["targetChanges"]) == 2
    assert client.get(workout["url"], headers=workout["headers"]).json()["effectiveWorkout"] == snapshot
    response = client.patch(workout["url"] + "/order", headers=workout["headers"], json=payload).json()
    assert {item["exercise"]["id"]: item["prescription"] for item in _flat(response["effectiveWorkout"])} == {
        item["exercise"]["id"]: item["prescription"] for item in _flat(snapshot)}
    payload.update(expectedRevision=1, clientOperationId=str(uuid.uuid4()), applyRevisedTargets=True)
    applied = client.patch(workout["url"] + "/order", headers=workout["headers"], json=payload).json()
    assert applied["effectiveWorkout"] == preview.json()["effectiveWorkout"]
    assert sum(len(block["items"]) for block in applied["effectiveWorkout"]["exerciseBlocks"] if block["blockType"] == "main") == 2
    assert applied["workoutSnapshot"] == snapshot


@pytest.mark.parametrize("protection", ["program", "unknown", "logged", "deleted", "custom-targets", "isolation"])
def test_preview_preserves_protected_targets(workout, protection):
    snapshot = _canonical_roles(workout, program=protection == "program", unknown=protection == "unknown")
    if protection in {"logged", "deleted"}:
        logged = _log(workout, workout["ids"][2])
        if protection == "deleted":
            client.delete(f"{workout['url']}/sets/{logged['id']}", headers=workout["headers"], params={"clientOperationId": str(uuid.uuid4())})
    if protection in {"custom-targets", "isolation"}:
        with SessionLocal() as db:
            session = db.get(WorkoutSession, workout["session"]["id"])
            effective = copy.deepcopy(snapshot)
            if protection == "custom-targets":
                _flat(effective)[2]["prescription"]["sets"] = 8
            else:
                db.get(Exercise, workout["ids"][2]).exercise_type = "accessory"
            session.arranged_workout = effective
            db.commit()
    preview = client.post(workout["url"] + "/order/preview", headers=workout["headers"], json=_order(workout))
    assert preview.status_code == 200
    assert preview.json()["targetChanges"] == []
    assert preview.json()["targetsChanged"] is False


def test_ownership_and_terminal_session_protection(workout):
    other = _headers(_token())
    assert client.patch(workout["url"] + "/order", headers=other, json=_order(workout)).status_code == 404
    assert client.get(f"{workout['url']}/exercises/{workout['ids'][0]}/replacement-candidates", headers=other).status_code == 404
    client.patch(workout["url"] + "/complete", headers=workout["headers"])
    assert _replace(workout).status_code == 409
    assert client.patch(workout["url"] + "/order", headers=workout["headers"], json=_order(workout)).status_code == 409


def test_inactive_original_has_explicit_unavailable_replacements(workout):
    with SessionLocal() as db:
        db.get(Exercise, workout["ids"][0]).is_active = False
        db.commit()
    response = client.get(f"{workout['url']}/exercises/{workout['ids'][0]}/replacement-candidates", headers=workout["headers"])
    assert response.status_code == 422


def test_replacement_load_comes_only_from_replacement_history(workout):
    import datetime
    from app.models.set_log import SetLog

    _canonical_roles(workout)
    with SessionLocal() as db:
        active = db.get(WorkoutSession, workout["session"]["id"])
        effective = copy.deepcopy(active.effective_workout)
        _flat(effective)[0]["prescription"].update(
            suggestedWeightKg=90, suggestedPreviousWeightKg=87.5, suggestedWeightReason="increase")
        active.arranged_workout = effective
        historical = WorkoutSession(user_id=workout["user"], workout_day_id=str(uuid.uuid4()),
                                    workout_date=datetime.date.today() - datetime.timedelta(days=1),
                                    day_type="upper", client_session_id=str(uuid.uuid4()), status="completed",
                                    completed_at=datetime.datetime.now(datetime.timezone.utc))
        db.add(historical)
        db.flush()
        db.add(SetLog(session_id=historical.id, exercise_id=workout["ids"][3], set_number=1,
                      reps=8, weight_kg=30, client_operation_id=str(uuid.uuid4())))
        db.commit()
    response = _replace(workout)
    assert response.status_code == 200
    prescription = _flat(response.json()["effectiveWorkout"])[0]["prescription"]
    assert prescription["suggestedWeightKg"] == 30
    assert prescription["suggestedPreviousWeightKg"] == 30
    assert prescription["suggestedWeightReason"] == "hold"


def test_replacement_without_history_clears_old_load_and_retry_returns_latest(workout):
    with SessionLocal() as db:
        active = db.get(WorkoutSession, workout["session"]["id"])
        effective = copy.deepcopy(active.effective_workout)
        _flat(effective)[0]["prescription"].update(suggestedWeightKg=90, suggestedPreviousWeightKg=87.5,
                                                  suggestedWeightReason="increase")
        active.arranged_workout = effective
        db.commit()
    operation_id = str(uuid.uuid4())
    response = _replace(workout, clientOperationId=operation_id)
    assert response.status_code == 200
    assert _flat(response.json()["effectiveWorkout"])[0]["prescription"]["suggestedWeightKg"] is None
    reordered_ids = [workout["ids"][2], workout["ids"][3], workout["ids"][1]]
    reordered = client.patch(workout["url"] + "/order", headers=workout["headers"],
                             json=_order(workout, reordered_ids, expectedRevision=1))
    assert reordered.status_code == 200
    retry = _replace(workout, clientOperationId=operation_id)
    assert retry.status_code == 200
    assert retry.json()["arrangementRevision"] == 2
    assert retry.json()["effectiveWorkout"] == reordered.json()["effectiveWorkout"]


def test_intervening_set_protects_targets_after_preview(workout):
    snapshot = _canonical_roles(workout)
    payload = _order(workout)
    preview = client.post(workout["url"] + "/order/preview", headers=workout["headers"], json=payload)
    assert preview.json()["targetsChanged"]
    _log(workout, workout["ids"][2])
    response = client.patch(workout["url"] + "/order", headers=workout["headers"],
                             json={**payload, "applyRevisedTargets": True})
    assert response.status_code == 200
    assert {item["exercise"]["id"]: item["prescription"] for item in _flat(response.json()["effectiveWorkout"])} == {
        item["exercise"]["id"]: item["prescription"] for item in _flat(snapshot)}


def test_replacement_refreshes_after_concurrent_set_writer(workout):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from sqlalchemy import event
    from app.core import workout_arrangement_service as arrangement
    from app.core.database import engine
    from app.models.set_log import SetLog

    replacing = threading.Event()
    session_id = workout["session"]["id"]

    def observe_lock(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement and threading.current_thread().name.startswith("replacement-test"):
            replacing.set()

    def replace_while_set_is_uncommitted():
        with SessionLocal() as db:
            # Exercise the identity-map refresh path as well as the row lock.
            db.get(WorkoutSession, session_id)
            with pytest.raises(arrangement.ArrangementConflict):
                arrangement.replace_exercise(db, session_id, workout["user"], workout["ids"][0],
                                             workout["ids"][3], 0, str(uuid.uuid4()))

    event.listen(engine, "before_cursor_execute", observe_lock)
    try:
        with SessionLocal() as writer:
            writer.query(WorkoutSession).filter_by(id=session_id).with_for_update().one()
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="replacement-test") as executor:
                future = executor.submit(replace_while_set_is_uncommitted)
                try:
                    assert replacing.wait(timeout=5), "Replacement did not reach its session lock"
                    writer.add(SetLog(session_id=session_id, exercise_id=workout["ids"][0], set_number=1,
                                      reps=8, weight_kg=20, client_operation_id=str(uuid.uuid4())))
                    writer.commit()
                finally:
                    writer.rollback()
                future.result(timeout=5)
    finally:
        event.remove(engine, "before_cursor_execute", observe_lock)
    current = client.get(workout["url"], headers=workout["headers"]).json()
    assert current["arrangementRevision"] == 0
    assert current["setLogs"][0]["exerciseId"] == workout["ids"][0]
