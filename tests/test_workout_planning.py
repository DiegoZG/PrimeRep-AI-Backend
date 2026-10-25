"""Trainer-led profiles, actual estimates and explicit planning application."""
import copy
import datetime
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.core.workout_planning import apply_time_budget, estimate_minutes, WorkoutTimeBudgetTooShort
from app.models.onboarding_profile import OnboardingProfile
from app.models.workout_week_plan import WorkoutWeekPlan
from conftest import LEGAL_ACCEPTANCE
from workout_test_utils import install_startable_workout

client = TestClient(app)
TODAY = datetime.date.today()
MONDAY = TODAY - datetime.timedelta(days=TODAY.weekday())


def _account(experience="beginner"):
    response = client.post("/v1/auth/signup", json={"email": f"planning_{uuid.uuid4().hex}@example.com",
        "password": "StrongPass123", "preferred_name": "Planning", "legalAcceptance": LEGAL_ACCEPTANCE,
        "onboarding": {"experienceLevel": experience, "workoutSplit": "full-body", "workoutFrequency": "3-days",
                       "selectedEquipment": [item["id"] for item in client.get("/v1/equipment").json()["items"]]}})
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    user_id = client.get("/v1/users/me", headers=headers).json()["id"]
    return headers, user_id


def _save(headers, **fields):
    return client.patch("/v1/users/me/training-preferences", headers=headers,
                        params={"planUpdateScope": "preserve"}, json=fields)


def _day(headers, when=TODAY):
    return install_startable_workout(client, headers["Authorization"].split()[1], workout_date=when,
                                     exercise_ids=("push_up", "bench_press"))


def _patch(headers, day, mode="trainer_led", minutes=None):
    return client.patch("/v1/workouts/week/planning", headers=headers, json={
        "workoutDayId": day["workoutDayId"], "weekStart": (datetime.date.fromisoformat(day["date"]) - datetime.timedelta(days=datetime.date.fromisoformat(day["date"]).weekday())).isoformat(),
        "effectiveDate": TODAY.isoformat(), "planningMode": mode, "timeBudgetMinutes": minutes})


def _apply(headers):
    return client.post("/v1/workouts/week/apply-planning-preference", headers=headers,
                       json={"weekStart": MONDAY.isoformat(), "effectiveDate": TODAY.isoformat()})


def _blocks(main=2, accessory=2):
    def item(index, sets, minimum, maximum, rest):
        return {"exercise": {"id": str(index)}, "prescription": {"sets": sets, "repsMin": minimum, "repsMax": maximum, "restSeconds": rest}}
    return [{"blockType": "main", "items": [item(i, 4, 6, 10, 120) for i in range(main)]},
            {"blockType": "accessory", "items": [item(100+i, 3, 10, 15, 75) for i in range(accessory)]}]


def test_estimate_uses_actual_prescriptions_and_never_budget_padding():
    assert estimate_minutes(_blocks(2, 2)) == 35
    assert estimate_minutes(_blocks(3, 3)) == 50
    assert estimate_minutes(_blocks(3, 4)) == 60
    full = {"exerciseBlocks": _blocks(2, 2)}
    short = apply_time_budget(full, "time_budget", 25)
    assert [len(block["items"]) for block in short["exerciseBlocks"]] == [2]
    assert short["estimatedMinutes"] == 25
    long = apply_time_budget(full, "time_budget", 120)
    assert long["estimatedMinutes"] == 35
    assert long["durationMinutes"] == 120
    assert long["exerciseBlocks"] == full["exerciseBlocks"]
    assert len(full["exerciseBlocks"][1]["items"]) == 2
    with pytest.raises(WorkoutTimeBudgetTooShort) as error:
        apply_time_budget(full, "time_budget", 20)
    assert error.value.minimum_useful_minutes == 25


def test_sparse_equipment_floor_keeps_available_work_instead_of_requiring_two_mains():
    one = {"exerciseBlocks": _blocks(0, 1)}
    result = apply_time_budget(one, "time_budget", 15)
    assert result["estimatedMinutes"] == 10
    assert len(result["exerciseBlocks"][0]["items"]) == 1


@pytest.mark.parametrize("experience,counts,estimate", [("no-experience", [2, 2], 35), ("beginner", [2, 2], 35),
                                                         ("intermediate", [3, 3], 50), ("advanced", [3, 4], 60)])
def test_new_accounts_get_finite_trainer_led_profile(experience, counts, estimate):
    headers, _ = _account(experience)
    preferences = client.get("/v1/users/me/training-preferences", headers=headers).json()
    assert preferences["workoutPlanningMode"] == "trainer_led"
    assert preferences["preferredWorkoutMinutes"] == 35
    week = client.get("/v1/workouts/week", headers=headers)
    assert week.status_code == 200, week.text
    day = week.json()["workouts"][0]
    assert [len(block["items"]) for block in day["exerciseBlocks"]] == counts
    assert day["estimatedMinutes"] == estimate
    assert day["timeBudgetMinutes"] is None
    assert day["planningMode"] == "trainer_led"
    assert day["planningSource"] == "default"


def test_legacy_missing_preference_and_stored_day_remain_timed_without_storage_mutation():
    headers, user_id = _account()
    original = _day(headers)
    original["durationMinutes"] = 45
    for field in ("planningMode", "timeBudgetMinutes", "planningSource", "planningEditable"):
        original.pop(field, None)
    with SessionLocal() as db:
        profile = db.query(OnboardingProfile).filter_by(user_id=user_id).one()
        profile.data = {key: value for key, value in profile.data.items() if key not in {"workoutPlanningMode", "preferredWorkoutMinutes"}}
        plan = db.query(WorkoutWeekPlan).filter_by(user_id=user_id, week_start_date=MONDAY).one()
        payload = copy.deepcopy(plan.plan_json)
        payload["workouts"] = [original]
        plan.plan_json = payload
        db.commit()
    prefs = client.get("/v1/users/me/training-preferences", headers=headers).json()
    assert prefs["workoutPlanningMode"] == "time_budget" and prefs["preferredWorkoutMinutes"] == 35
    response = client.get("/v1/workouts/week", headers=headers).json()["workouts"][0]
    assert response["planningMode"] == "time_budget" and response["timeBudgetMinutes"] == 45
    assert sum(len(block["items"]) for block in response["exerciseBlocks"]) == 2
    with SessionLocal() as db:
        stored = db.query(WorkoutWeekPlan).filter_by(user_id=user_id, week_start_date=MONDAY).one().plan_json
        assert stored["workouts"][0] == original
        assert "workoutPlanningMode" not in db.query(OnboardingProfile).filter_by(user_id=user_id).one().data


def test_old_onboarding_upsert_keeps_new_account_default_and_remembered_minutes():
    headers, _ = _account()
    assert _save(headers, preferredWorkoutMinutes=90).status_code == 200
    response = client.post("/v1/onboarding/me", headers=headers, json={"data": {"fitnessGoal": "get-stronger"}})
    assert response.status_code == 200
    prefs = client.get("/v1/users/me/training-preferences", headers=headers).json()
    assert prefs["workoutPlanningMode"] == "trainer_led" and prefs["preferredWorkoutMinutes"] == 90


@pytest.mark.parametrize("invalid", [14, 121, True, None, "90"])
def test_preference_budget_validation(invalid):
    headers, _ = _account()
    assert _save(headers, preferredWorkoutMinutes=invalid).status_code == 422


def test_save_preserve_keeps_current_plan_and_apply_explicitly_resets_override():
    headers, user_id = _account("advanced")
    day = _day(headers)
    override = _patch(headers, day, "time_budget", 25)
    assert override.status_code == 200, override.text
    before = override.json()
    assert before["workouts"][0]["planningSource"] == "override"
    saved = _save(headers, workoutPlanningMode="trainer_led", preferredWorkoutMinutes=120)
    assert saved.status_code == 200
    unchanged = client.get("/v1/workouts/week", headers=headers).json()
    assert unchanged["workouts"][0] == before["workouts"][0]
    applied = _apply(headers)
    assert applied.status_code == 200, applied.text
    assert applied.json()["updatedWorkoutCount"] == 1
    updated = applied.json()["weekPlan"]["workouts"][0]
    assert updated["planningSource"] == "default" and updated["planningMode"] == "trainer_led"
    assert sum(len(block["items"]) for block in updated["exerciseBlocks"]) == 7
    assert client.get("/v1/users/me/training-preferences", headers=headers).json()["preferredWorkoutMinutes"] == 120


def test_too_small_apply_is_atomic_but_valid_settings_remain_saved():
    headers, user_id = _account()
    _day(headers)
    with SessionLocal() as db:
        before = copy.deepcopy(db.query(WorkoutWeekPlan).filter_by(user_id=user_id, week_start_date=MONDAY).one().plan_json)
    assert _save(headers, workoutPlanningMode="time_budget", preferredWorkoutMinutes=15).status_code == 200
    response = _apply(headers)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "workout_time_budget_too_short"
    assert response.json()["detail"]["minimumUsefulMinutes"] == 25
    assert client.get("/v1/users/me/training-preferences", headers=headers).json()["preferredWorkoutMinutes"] == 15
    with SessionLocal() as db:
        assert db.query(WorkoutWeekPlan).filter_by(user_id=user_id, week_start_date=MONDAY).one().plan_json == before


def test_started_completed_and_past_days_are_protected_from_override_and_bulk_apply():
    headers, user_id = _account()
    day = _day(headers)
    started = client.post("/v1/workouts/sessions", headers=headers, json={"workoutDayId": day["workoutDayId"], "workoutDate": day["date"], "dayType": day["dayType"]})
    assert started.status_code == 201
    assert _patch(headers, day).status_code == 409
    assert _apply(headers).json()["updatedWorkoutCount"] == 0
    assert client.get("/v1/workouts/week", headers=headers).json()["workouts"][0]["planningEditable"] is False
    client.patch(f"/v1/workouts/sessions/{started.json()['id']}/complete", headers=headers)
    assert _patch(headers, day).status_code == 409
    assert _apply(headers).json()["protectedWorkoutCount"] == 1
    original = client.get(f"/v1/workouts/sessions/{started.json()['id']}", headers=headers).json()["workoutSnapshot"]
    assert original == day
    past = _day(headers, TODAY - datetime.timedelta(days=1))
    assert _patch(headers, past).status_code == 409


def test_long_budgets_do_not_add_junk_volume_and_new_next_response_has_mode():
    headers, _ = _account("advanced")
    day = _day(headers)
    shapes = []
    for minutes in [60, 75, 90, 105, 120]:
        response = _patch(headers, day, "time_budget", minutes)
        assert response.status_code == 200, response.text
        result = response.json()["workouts"][0]
        shapes.append(result["exerciseBlocks"])
        assert result["timeBudgetMinutes"] == minutes and result["estimatedMinutes"] == 60
    assert all(shape == shapes[0] for shape in shapes)
    next_response = client.post("/v1/workouts/next", headers=headers)
    assert next_response.status_code == 200
    assert next_response.json()["planningMode"] == "time_budget"


def test_mode_summary_does_not_promote_a_trainer_estimate_to_a_time_goal():
    from app.core.workout_planning import planning_summary
    assert planning_summary({"planningMode": "trainer_led", "estimatedMinutes": 60, "exerciseBlocks": _blocks(3, 4)}) == "7 exercises"
    assert planning_summary({"planningMode": "time_budget", "timeBudgetMinutes": 120, "estimatedMinutes": 35}) == "Up to 120 min"
    assert planning_summary({"programActivationId": "program", "durationMinutes": 45}) == "45 min · Program"


def test_trimmed_custom_muscles_are_reported_as_deferred():
    headers, _ = _account("advanced")
    assert _save(headers, workoutSplit="custom", customWorkouts=[{
        "id": "coverage", "name": "Coverage", "type": "custom", "muscleGroups": ["chest", "back", "quads"]}]).status_code == 200
    future = MONDAY + datetime.timedelta(days=7)
    week = client.get("/v1/workouts/week", headers=headers, params={"weekStart": future.isoformat()}).json()
    day = week["workouts"][0]
    response = _patch(headers, day, "time_budget", 25)
    assert response.status_code == 200
    result = response.json()["workouts"][0]
    kept = {item["exercise"]["primaryMuscle"] for block in result["exerciseBlocks"] for item in block["items"]}
    assert len(kept) == 2
    assert kept | set(result["deferredMuscles"]) == {"chest", "back", "quads"}
    assert result["unavailableMuscles"] == []


@pytest.mark.parametrize("mode,minutes", [("trainer_led", 35), ("time_budget", 25)])
def test_standard_days_do_not_invent_custom_rotation_metadata(mode, minutes):
    headers, _ = _account()
    assert _save(headers, workoutSplit="upper-lower", workoutPlanningMode=mode,
                 preferredWorkoutMinutes=minutes).status_code == 200
    future = MONDAY + datetime.timedelta(days=7)
    response = client.get("/v1/workouts/week", headers=headers, params={"weekStart": future.isoformat()})
    assert response.status_code == 200, response.text
    days = response.json()["workouts"]
    assert any(day["dayType"] == "lower" for day in days)
    for day in days:
        assert day["deferredMuscles"] == []
        assert day["unavailableMuscles"] == []


def test_fully_protected_program_week_and_pending_refresh_are_not_rewritten():
    headers, user_id = _account()
    day = _day(headers)
    with SessionLocal() as db:
        plan = db.query(WorkoutWeekPlan).filter_by(user_id=user_id, week_start_date=MONDAY).one()
        payload = copy.deepcopy(plan.plan_json)
        payload["workouts"][0].update(programActivationId="authored-program", templateId="authored-template")
        payload["programActivationId"] = "authored-program"
        plan.plan_json = payload
        db.commit()
        before = copy.deepcopy(plan.plan_json)
    assert _patch(headers, day).status_code == 409
    response = _apply(headers)
    assert response.status_code == 200, response.text
    assert response.json()["updatedWorkoutCount"] == 0
    result = response.json()["weekPlan"]["workouts"][0]
    assert result["planningMode"] == "trainer_led" and result["timeBudgetMinutes"] is None
    assert result["planningEditable"] is False
    with SessionLocal() as db:
        assert db.query(WorkoutWeekPlan).filter_by(user_id=user_id, week_start_date=MONDAY).one().plan_json == before


def test_bulk_failure_after_staging_a_current_day_rolls_back_all_future_weeks():
    from app.models.exercise import Exercise
    headers, user_id = _account()
    day = _day(headers)
    assert _save(headers, workoutSplit="custom", customWorkouts=[
        {"id": "one", "name": "One", "type": "custom", "muscleGroups": ["neck"]},
        {"id": "two", "name": "Two", "type": "custom", "muscleGroups": ["chest"]},
    ], workoutPlanningMode="time_budget", preferredWorkoutMinutes=15).status_code == 200
    future = MONDAY + datetime.timedelta(days=7)
    with SessionLocal() as db:
        db.add(Exercise(id=f"planning-neck-{uuid.uuid4().hex}", name="Only neck exercise", exercise_type="strength",
                        primary_muscle="neck", secondary_muscles=[], source="user", owner_user_id=user_id))
        current = db.query(WorkoutWeekPlan).filter_by(user_id=user_id, week_start_date=MONDAY).one()
        data = copy.deepcopy(current.plan_json)
        data["workouts"][0]["dayType"] = "custom:one"
        current.plan_json = data
        later = copy.deepcopy(data)
        later["weekStart"] = future.isoformat()
        later["workouts"][0].update(workoutDayId=str(uuid.uuid4()), date=future.isoformat(), dayType="custom:two")
        db.add(WorkoutWeekPlan(user_id=user_id, week_start_date=future, days_per_week=1, plan_json=later))
        db.commit()
        before = {plan.id: copy.deepcopy(plan.plan_json) for plan in db.query(WorkoutWeekPlan).filter_by(user_id=user_id).all()}
    response = _apply(headers)
    assert response.status_code == 409, response.text
    with SessionLocal() as db:
        assert {plan.id: plan.plan_json for plan in db.query(WorkoutWeekPlan).filter_by(user_id=user_id).all()} == before


def test_scheduled_program_boundary_protects_future_generated_rows_and_current_overrides_reset():
    from app.models.workout_template import UserProgramActivation
    headers, user_id = _account("advanced")
    day = _day(headers)
    assert _patch(headers, day, "time_budget", 25).status_code == 200
    future = MONDAY + datetime.timedelta(days=7)
    with SessionLocal() as db:
        current = db.query(WorkoutWeekPlan).filter_by(user_id=user_id, week_start_date=MONDAY).one()
        payload = copy.deepcopy(current.plan_json)
        payload["weekStart"] = future.isoformat()
        payload["workouts"][0].update(workoutDayId=str(uuid.uuid4()), date=future.isoformat())
        db.add(WorkoutWeekPlan(user_id=user_id, week_start_date=future, days_per_week=1, plan_json=payload))
        db.add(UserProgramActivation(user_id=user_id, template_version=1, status="scheduled", effective_date=future,
                                    weekdays=[0], activation_snapshot={}, apply_mode="next-week",
                                    client_operation_id=str(uuid.uuid4()), request_fingerprint="fixture"))
        db.commit()
        before_future = copy.deepcopy(db.query(WorkoutWeekPlan).filter_by(user_id=user_id, week_start_date=future).one().plan_json)
    response = _apply(headers)
    assert response.status_code == 200, response.text
    assert response.json()["updatedWorkoutCount"] == 1 and response.json()["protectedWorkoutCount"] == 1
    assert response.json()["weekPlan"]["workouts"][0]["planningSource"] == "default"
    with SessionLocal() as db:
        assert db.query(WorkoutWeekPlan).filter_by(user_id=user_id, week_start_date=future).one().plan_json == before_future


def test_legacy_duration_adapter_and_skip_use_the_same_saved_profile_policy():
    headers, _ = _account("intermediate")
    day = _day(headers)
    response = client.patch("/v1/workouts/week/duration", headers=headers,
                            json={"workoutDayId": day["workoutDayId"], "durationMinutes": 90})
    assert response.status_code == 200, response.text
    edited = response.json()["workouts"][0]
    assert edited["planningMode"] == "time_budget" and edited["timeBudgetMinutes"] == 90
    assert edited["estimatedMinutes"] == 50 and edited["planningSource"] == "override"
    assert _save(headers, workoutPlanningMode="trainer_led", preferredWorkoutMinutes=120).status_code == 200
    skipped = client.post("/v1/workouts/week/skip", headers=headers, json={"workoutDayId": edited["workoutDayId"], "weekStart": MONDAY.isoformat()})
    assert skipped.status_code == 200, skipped.text
    replacement = skipped.json()["workouts"][-1]
    assert replacement["planningMode"] == "trainer_led" and replacement["timeBudgetMinutes"] is None
    assert replacement["estimatedMinutes"] == 50


def test_concurrent_real_session_start_wins_the_schedule_lock_before_bulk_apply():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import event
    from app.core.database import engine
    from app.core.workout_logging_service import create_session
    from app.core.workout_planning_service import apply_planning_preference

    headers, user_id = _account("advanced")
    day = _day(headers)
    held = threading.Event()
    release = threading.Event()
    planning_waits = threading.Event()

    def start():
        with SessionLocal() as db:
            commit = db.commit
            def held_commit():
                held.set()
                assert release.wait(5)
                commit()
            db.commit = held_commit
            return create_session(db, user_id, day["workoutDayId"], TODAY, day["dayType"], str(uuid.uuid4())).id

    def apply():
        with SessionLocal() as db:
            return apply_planning_preference(db, user_id=user_id, week_start=MONDAY, effective_date=TODAY)

    def observe(conn, cursor, statement, parameters, context, executemany):
        if threading.current_thread().name.startswith("planning-apply") and "FOR UPDATE" in statement and "users" in statement:
            planning_waits.set()

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="planning-start") as starter, ThreadPoolExecutor(max_workers=1, thread_name_prefix="planning-apply") as planner:
            started = starter.submit(start)
            assert held.wait(5)
            applying = planner.submit(apply)
            try:
                assert planning_waits.wait(5)
            finally:
                release.set()
            session_id = started.result(5)
            result = applying.result(5)
        assert result["updatedWorkoutCount"] == 0 and result["protectedWorkoutCount"] == 1
        saved = client.get(f"/v1/workouts/sessions/{session_id}", headers=headers).json()
        assert saved["workoutSnapshot"] == day and saved["effectiveWorkout"] == day
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", observe)
