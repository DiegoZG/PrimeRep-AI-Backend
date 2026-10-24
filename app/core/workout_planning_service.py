"""Atomic day overrides and explicit application of saved planning defaults."""
import copy
import random
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.schedule_lock import lock_user_schedule
from app.core.training_preferences_service import _resolve_mutation_calendar
from app.core.workout_planning import WorkoutPlanningLocked
from app.core.workout_service import get_onboarding_settings, get_eligible_exercises, _standard_day
from app.core.workout_week_service import (
    _generate_single_workout, _get_owned_equipment_ids_from_settings, _plan_json_to_response,
    _resolve_plan_for_workout_day, get_or_create_week_plan, get_week_plan, get_week_start, ProgramDurationLockedError,
)
from app.models.workout_session import WorkoutSession
from app.models.workout_template import UserProgramActivation
from app.models.workout_week_plan import WorkoutWeekPlan


def _protected_ids(db, user_id):
    return {row[0] for row in db.query(WorkoutSession.workout_day_id).filter(
        WorkoutSession.user_id == user_id, WorkoutSession.status.in_(["in_progress", "completed"])).all()}


def _regenerate(db, user_id, week_start, day, settings, mode, minutes, source):
    definition = next((item for item in settings.day_cycle if item.day_type == day["dayType"]), _standard_day(day["dayType"]))
    workout_date = date.fromisoformat(day["date"])
    seed = f"{user_id}:{week_start.isoformat()}:{settings.selection_seed}:{workout_date.isoformat()}:{definition.day_type}"
    result, _ = _generate_single_workout(
        rng=random.Random(seed), all_eligible=get_eligible_exercises(
            db, owned_equipment_ids=_get_owned_equipment_ids_from_settings(db, settings), user_id=user_id),
        day_type=definition.day_type, split_preference=settings.split_preference, day_definition=definition,
        duration_minutes=minutes or settings.preferred_workout_minutes, workout_date=workout_date,
        slot_index=day.get("slotIndex", 0), workout_day_id=day["workoutDayId"], db=db, user_id=user_id,
        planning_mode=mode, experience_level=settings.experience_level, planning_source=source)
    if "cycleIndex" in day:
        result["cycleIndex"] = day["cycleIndex"]
    return result


def update_day_planning(db: Session, *, user_id: str, workout_day_id: str, planning_mode: str,
                        time_budget_minutes, week_start=None, effective_date=None):
    effective_date = effective_date or datetime.now(timezone.utc).date()
    if week_start is not None:
        # The compatibility duration endpoint accepts an explicit future week.
        if effective_date not in {datetime.now(timezone.utc).date() + timedelta(days=i) for i in (-1, 0, 1)}:
            raise ValueError("effectiveDate must be the caller's current local date")
    lock_user_schedule(db, user_id)
    plan = _resolve_plan_for_workout_day(db, user_id=user_id, workout_day_id=workout_day_id,
                                       week_start=week_start or get_week_start(effective_date))
    if plan is None:
        return None
    days = copy.deepcopy(plan.plan_json.get("workouts", []))
    day = next((item for item in days if item["workoutDayId"] == workout_day_id), None)
    if day is None:
        return None
    if day.get("programActivationId") or day.get("templateId") or day.get("splitKey") == "program":
        raise ProgramDurationLockedError("Program workouts keep the targets authored by their program")
    if date.fromisoformat(day["date"]) < effective_date or workout_day_id in _protected_ids(db, user_id):
        raise WorkoutPlanningLocked("Planning cannot change a past, started or completed workout")
    settings = get_onboarding_settings(db, user_id=user_id)
    days[days.index(day)] = _regenerate(db, user_id, plan.week_start_date, day, settings,
                                      planning_mode, time_budget_minutes, "override")
    plan.plan_json = {**plan.plan_json, "workouts": days,
                      "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    db.commit()
    db.refresh(plan)
    return _plan_json_to_response(plan.plan_json)


def apply_planning_preference(db: Session, *, user_id: str, week_start: date, effective_date: date):
    week_start, effective_date = _resolve_mutation_calendar(week_start=week_start, effective_date=effective_date)
    lock_user_schedule(db, user_id)
    if get_week_plan(db, user_id=user_id, week_start=week_start) is None:
        get_or_create_week_plan(db, user_id=user_id, week_start=week_start, commit=False)
    settings = get_onboarding_settings(db, user_id=user_id)
    protected = _protected_ids(db, user_id)
    programs = db.query(UserProgramActivation).filter(
        UserProgramActivation.user_id == user_id, UserProgramActivation.status.in_(["active", "scheduled"])).all()
    boundary = min((item.effective_date for item in programs), default=None)
    plans = db.query(WorkoutWeekPlan).filter(WorkoutWeekPlan.user_id == user_id,
                                           WorkoutWeekPlan.week_start_date >= week_start).order_by(WorkoutWeekPlan.week_start_date).all()
    staged = []
    changed = skipped = 0
    for plan in plans:
        days = []
        week_changed = False
        for day in plan.plan_json.get("workouts", []):
            workout_date = date.fromisoformat(day["date"])
            if (day.get("programActivationId") or day.get("templateId") or day.get("splitKey") == "program"
                    or plan.plan_json.get("programActivationId") or workout_date < effective_date
                    or day["workoutDayId"] in protected or (boundary and workout_date >= boundary)):
                days.append(copy.deepcopy(day))
                skipped += 1
            else:
                days.append(_regenerate(db, user_id, plan.week_start_date, day, settings,
                                        settings.planning_mode, settings.preferred_workout_minutes, "default"))
                changed += 1
                week_changed = True
        if not week_changed:
            continue
        payload = {**plan.plan_json, "workouts": days, "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
        payload.pop("preferencesRefreshRequired", None)
        staged.append((plan, payload))
    for plan, payload in staged:
        plan.plan_json = payload
    db.commit()
    current = next(plan for plan in plans if plan.week_start_date == week_start)
    db.refresh(current)
    return {"weekPlan": _plan_json_to_response(current.plan_json),
            "updatedWorkoutCount": changed, "protectedWorkoutCount": skipped}


def planning_editability(db, user_id, response):
    protected = _protected_ids(db, user_id)
    earliest_today = datetime.now(timezone.utc).date() - timedelta(days=1)
    for day in response.workouts:
        day.planning_editable = not (day.program_activation_id or day.template_id or day.split_key == "program"
                                     or day.workout_day_id in protected or day.date < earliest_today)
    return response
