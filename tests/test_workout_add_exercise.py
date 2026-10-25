"""Manual additions preserve a generated workout and serialize with starting it."""
import copy
import datetime
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.database import SessionLocal, engine
from app.core.workout_logging_service import create_session
from app.core.workout_planning import WorkoutPlanningLocked, estimate_minutes, planning_summary
from app.core.workout_planning_service import add_workout_exercise
from app.main import app
from app.models.exercise import Exercise
from app.models.equipment import Equipment
from app.models.onboarding_profile import OnboardingProfile
from app.models.set_log import SetLog
from app.models.workout_session import WorkoutSession
from app.models.workout_week_plan import WorkoutWeekPlan
from conftest import LEGAL_ACCEPTANCE

client = TestClient(app)
TODAY = datetime.date.today()
MONDAY = TODAY - datetime.timedelta(days=TODAY.weekday())


def _items(day):
    return [item for block in day["exerciseBlocks"] for item in block["items"]]


def _ids(day):
    return [item["exercise"]["id"] for item in _items(day)]


@pytest.fixture
def workout():
    response = client.post("/v1/auth/signup", json={"email": f"add_{uuid.uuid4().hex}@example.com",
        "password": "StrongPass123", "preferred_name": "Add exercise", "legalAcceptance": LEGAL_ACCEPTANCE,
        "onboarding": {"experienceLevel": "intermediate", "workoutSplit": "full-body", "workoutFrequency": "3-days",
            "selectedEquipment": [item["id"] for item in client.get("/v1/equipment").json()["items"]]}})
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    user_id = client.get("/v1/users/me", headers=headers).json()["id"]
    response = client.get("/v1/workouts/week", params={"weekStart": MONDAY.isoformat()}, headers=headers)
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        plan = db.query(WorkoutWeekPlan).filter_by(user_id=user_id, week_start_date=MONDAY).one()
        payload = copy.deepcopy(plan.plan_json)
        payload["workouts"][0]["date"] = TODAY.isoformat()
        plan.plan_json = payload
        db.commit()
        day = copy.deepcopy(payload["workouts"][0])
        profile = copy.deepcopy(db.get(OnboardingProfile, user_id).data)
    return {"headers": headers, "user_id": user_id, "day": day, "payload": payload, "profile": profile}


def _exercise(workout, **fields):
    fields = {"id": f"manual_{uuid.uuid4().hex}", "name": "Manual exercise", "exercise_type": "strength",
        "primary_muscle": "chest", "secondary_muscles": [], "source": "user", "owner_user_id": workout["user_id"],
        "generation_eligible": False, **fields}
    with SessionLocal() as db:
        exercise = Exercise(**fields)
        db.add(exercise)
        db.commit()
        return exercise.id


def _add(workout, exercise_id, **fields):
    return client.post("/v1/workouts/week/exercises", headers=workout["headers"], json={
        "workoutDayId": workout["day"]["workoutDayId"], "exerciseId": exercise_id,
        "expectedExerciseIds": _ids(workout["day"]), "weekStart": MONDAY.isoformat(),
        "effectiveDate": TODAY.isoformat(), **fields})


def _raw(workout):
    with SessionLocal() as db:
        return copy.deepcopy(db.query(WorkoutWeekPlan).filter_by(user_id=workout["user_id"], week_start_date=MONDAY).one().plan_json)


def _set_day(workout, **fields):
    with SessionLocal() as db:
        plan = db.query(WorkoutWeekPlan).filter_by(user_id=workout["user_id"], week_start_date=MONDAY).one()
        payload = copy.deepcopy(plan.plan_json)
        payload["workouts"][0].update(fields)
        plan.plan_json = payload
        db.commit()
    workout["day"].update(fields)


def test_append_preserves_prefix_other_days_preferences_and_start_logs_new_exercise(workout):
    exercise_id = _exercise(workout)
    response = _add(workout, exercise_id)
    assert response.status_code == 200, response.text
    changed = response.json()["workouts"][0]
    raw = _raw(workout)
    assert raw["workouts"][1:] == workout["payload"]["workouts"][1:]
    assert changed["exerciseBlocks"][:-1] == workout["day"]["exerciseBlocks"]
    added = changed["exerciseBlocks"][-1]
    assert added["blockType"] == "main"
    assert added["items"][0]["exercise"]["id"] == exercise_id
    assert added["items"][0]["prescription"] == {"sets": 3, "repsMin": 8, "repsMax": 12, "restSeconds": 90,
        "suggestedWeightKg": None, "suggestedPreviousWeightKg": None, "suggestedWeightReason": None}
    assert changed["estimatedMinutes"] == estimate_minutes(changed["exerciseBlocks"])
    assert changed["durationMinutes"] == changed["estimatedMinutes"]
    with SessionLocal() as db:
        assert db.get(OnboardingProfile, workout["user_id"]).data == workout["profile"]
        assert db.query(WorkoutSession).filter_by(user_id=workout["user_id"]).count() == 0
    started = client.post("/v1/workouts/sessions", headers=workout["headers"], json={
        "workoutDayId": changed["workoutDayId"], "workoutDate": TODAY.isoformat(), "dayType": changed["dayType"]})
    assert started.status_code == 201, started.text
    snapshot = started.json()["workoutSnapshot"]
    assert _ids(snapshot) == _ids(changed)
    assert sum(item["prescription"]["sets"] for item in _items(snapshot)) == 24
    logged = client.post(f"/v1/workouts/sessions/{started.json()['id']}/sets", headers=workout["headers"],
        json={"exerciseId": exercise_id, "setNumber": 1, "reps": 10})
    assert logged.status_code == 201, logged.text


def test_duplicate_lost_reply_retry_is_noop_even_after_other_append(workout):
    exercise_id = _exercise(workout)
    assert _add(workout, exercise_id).status_code == 200
    current = _raw(workout)
    second_id = _exercise(workout)
    assert _add(workout, second_id, expectedExerciseIds=_ids(current["workouts"][0])).status_code == 200
    before = _raw(workout)
    assert _add(workout, exercise_id).status_code == 200
    assert _raw(workout) == before
    stale = _add(workout, _exercise(workout))
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "workout_changed"
    assert _raw(workout) == before


def test_timed_overage_is_atomic_and_explicit_retry_never_trims(workout):
    _set_day(workout, planningMode="time_budget", timeBudgetMinutes=50, durationMinutes=50)
    before = _raw(workout)
    exercise_id = _exercise(workout)
    response = _add(workout, exercise_id)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "workout_time_budget_exceeded"
    assert response.json()["detail"]["timeBudgetMinutes"] == 50
    assert response.json()["detail"]["estimatedMinutes"] > 50
    assert _raw(workout) == before
    response = _add(workout, exercise_id, allowOverBudget=True)
    assert response.status_code == 200, response.text
    day = response.json()["workouts"][0]
    assert day["exerciseBlocks"][:-1] == workout["day"]["exerciseBlocks"]
    assert day["durationMinutes"] == day["timeBudgetMinutes"] == 50
    assert day["estimatedMinutes"] > 50


def test_manual_over_budget_summary_is_truthful_without_changing_other_modes():
    day = {"planningMode": "time_budget", "timeBudgetMinutes": 50, "durationMinutes": 50, "estimatedMinutes": 60, "exerciseBlocks": []}
    assert planning_summary(day) == "About 60 min · 50 min budget"
    assert planning_summary({**day, "estimatedMinutes": 45}) == "Up to 50 min"
    assert planning_summary({**day, "planningMode": "trainer_led", "timeBudgetMinutes": None}) == "0 exercises"
    assert planning_summary({**day, "programActivationId": "authored"}) == "50 min · Program"


@pytest.mark.parametrize("fields,status", [({"is_active": False}, 404), ({"source": "catalog", "owner_user_id": None, "publication_status": "draft"}, 404),
    ({"tracking_mode": "duration", "content_version": "reviewed"}, 422), ({"tracking_mode": "distance", "content_version": "reviewed"}, 422)])
def test_inactive_hidden_and_reference_only_exercises_are_rejected(workout, fields, status):
    before = _raw(workout)
    response = _add(workout, _exercise(workout, **fields))
    assert response.status_code == status, response.text
    assert _raw(workout) == before


def test_other_users_private_exercise_and_workout_are_unavailable(workout):
    owner = client.post("/v1/auth/signup", json={"email": f"owner_{uuid.uuid4().hex}@example.com", "password": "StrongPass123",
        "preferred_name": "Other", "legalAcceptance": LEGAL_ACCEPTANCE}).json()
    other_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    other_id = client.get("/v1/users/me", headers=other_headers).json()["id"]
    exercise_id = _exercise(workout, owner_user_id=other_id)
    before = _raw(workout)
    assert _add(workout, exercise_id).status_code == 404
    valid = _exercise(workout)
    request = {"workoutDayId": workout["day"]["workoutDayId"], "exerciseId": valid, "expectedExerciseIds": _ids(workout["day"]),
               "weekStart": MONDAY.isoformat(), "effectiveDate": TODAY.isoformat()}
    assert client.post("/v1/workouts/week/exercises", json=request).status_code in (401, 403)
    assert client.post("/v1/workouts/week/exercises", headers=other_headers, json=request).status_code == 404
    assert _raw(workout) == before


@pytest.mark.parametrize("source,status", [("seed", "legacy"), ("catalog", "published")])
def test_shared_manual_choice_does_not_require_generation_eligibility_or_owned_equipment(workout, source, status):
    exercise_id = _exercise(workout, source=source, owner_user_id=None, publication_status=status,
                            content_version="reviewed" if source == "catalog" else None, tracking_mode="reps")
    with SessionLocal() as db:
        equipment = db.query(Equipment).first()
        equipment_id = equipment.id
        db.get(Exercise, exercise_id).equipment = [equipment]
        profile = db.get(OnboardingProfile, workout["user_id"])
        profile.data = {**profile.data, "selectedEquipment": []}
        db.commit()
    response = _add(workout, exercise_id)
    assert response.status_code == 200, response.text
    assert _items(response.json()["workouts"][0])[-1]["exercise"]["requiredEquipmentIds"] == [equipment_id]


@pytest.mark.parametrize("protection", ["past", "program", "in_progress", "completed"])
def test_protected_days_reject_additions(workout, protection):
    if protection == "past":
        _set_day(workout, date=(TODAY - datetime.timedelta(days=1)).isoformat())
    elif protection == "program":
        _set_day(workout, programActivationId="authored")
    else:
        with SessionLocal() as db:
            db.add(WorkoutSession(user_id=workout["user_id"], workout_day_id=workout["day"]["workoutDayId"],
                workout_date=TODAY, day_type="full_body", status=protection, client_session_id=str(uuid.uuid4())))
            db.commit()
    before = _raw(workout)
    response = _add(workout, _exercise(workout))
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == ("program_duration_locked" if protection == "program" else "workout_planning_locked")
    assert _raw(workout) == before


def test_own_history_only_for_manually_chosen_load_and_no_equipment_filter(workout):
    exercise_id = _exercise(workout)
    with SessionLocal() as db:
        profile = db.get(OnboardingProfile, workout["user_id"])
        profile.data = {**profile.data, "selectedEquipment": []}
        history = WorkoutSession(user_id=workout["user_id"], workout_day_id=str(uuid.uuid4()), workout_date=TODAY - datetime.timedelta(days=1),
            day_type="upper", status="completed", client_session_id=str(uuid.uuid4()), completed_at=datetime.datetime.now(datetime.timezone.utc))
        db.add(history)
        db.flush()
        db.add(SetLog(session_id=history.id, exercise_id=exercise_id, set_number=1, reps=10, weight_kg=30, client_operation_id=str(uuid.uuid4())))
        db.commit()
    response = _add(workout, exercise_id)
    assert response.status_code == 200, response.text
    added = _items(response.json()["workouts"][0])[-1]
    assert added["prescription"]["suggestedWeightKg"] == 30
    assert added["prescription"]["suggestedWeightReason"] == "hold"


def test_real_session_start_and_addition_share_schedule_lock(workout):
    exercise_id = _exercise(workout)
    held, release, waiting = threading.Event(), threading.Event(), threading.Event()
    def start():
        with SessionLocal() as db:
            commit = db.commit
            def held_commit():
                held.set()
                assert release.wait(5)
                commit()
            db.commit = held_commit
            return create_session(db, workout["user_id"], workout["day"]["workoutDayId"], TODAY, workout["day"]["dayType"], str(uuid.uuid4())).id
    def add():
        with SessionLocal() as db:
            return add_workout_exercise(db, user_id=workout["user_id"], workout_day_id=workout["day"]["workoutDayId"],
                exercise_id=exercise_id, expected_exercise_ids=_ids(workout["day"]), week_start=MONDAY, effective_date=TODAY)
    def observe(conn, cursor, statement, parameters, context, executemany):
        if threading.current_thread().name.startswith("manual-add") and "FOR UPDATE" in statement and "users" in statement:
            waiting.set()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="start-before-add") as starter, ThreadPoolExecutor(max_workers=1, thread_name_prefix="manual-add") as adder:
            started = starter.submit(start)
            assert held.wait(5)
            adding = adder.submit(add)
            try:
                assert waiting.wait(5)
            finally:
                release.set()
            session_id = started.result(5)
            with pytest.raises(WorkoutPlanningLocked):
                adding.result(5)
        assert _raw(workout) == workout["payload"]
        response = client.get(f"/v1/workouts/sessions/{session_id}", headers=workout["headers"])
        assert _ids(response.json()["workoutSnapshot"]) == _ids(workout["day"])
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", observe)
