from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.onboarding_service import get_onboarding_by_user_id, upsert_onboarding
from app.core.schedule_lock import lock_user_schedule
from app.models.equipment import Equipment
from app.models.workout_session import WorkoutSession
from app.models.workout_week_plan import WorkoutWeekPlan
from app.models.workout_template import UserProgramActivation


PREFERENCE_FIELDS = (
    "fitnessGoal",
    "experienceLevel",
    "workoutFrequency",
    "workoutSplit",
    "trainingPlace",
    "selectedEquipment",
    "customWorkouts",
)

_LEGACY_FIELDS = {
    "fitnessGoal": ("fitness_goal", "goal"),
    "experienceLevel": ("experience_level",),
    "workoutFrequency": ("workout_frequency", "days_per_week"),
    "workoutSplit": ("workout_split", "split_preference"),
    "trainingPlace": ("training_place",),
    "selectedEquipment": ("selected_equipment", "equipment_ids"),
    "customWorkouts": ("custom_workouts",),
}

_GOAL_VALUES = {
    "build-muscle": "build-muscle",
    "build_muscle": "build-muscle",
    "hypertrophy": "build-muscle",
    "get-stronger": "get-stronger",
    "get_stronger": "get-stronger",
    "strength": "get-stronger",
    "general-fitness": "general-fitness",
    "general_fitness": "general-fitness",
    "conditioning": "conditioning",
}
_EXPERIENCE_VALUES = {
    "no-experience": "no-experience",
    "no_experience": "no-experience",
    "novice": "no-experience",
    "beginner": "beginner",
    "intermediate": "intermediate",
    "advanced": "advanced",
}
_SPLIT_VALUES = {
    "upper_lower": "upper-lower",
    "ppl": "push-pull-legs",
    "full_body": "full-body",
}
_PLACE_VALUES = {
    "large_gym": "large-gym",
    "small_gym": "small-gym",
    "garage_gym": "garage-gym",
    "home": "garage-gym",
}
_FREQUENCY_VALUES = {
    1: "1-day",
    2: "2-days",
    3: "3-days",
    4: "4-days",
    5: "5-days",
    6: "6-days",
    7: "every-day",
}


def get_training_preferences(db: Session, user_id: str) -> dict[str, Any]:
    profile = get_onboarding_by_user_id(db, user_id)
    data = profile.data if profile else {}
    return _canonical_preferences(data)


def update_training_preferences(
    db: Session,
    user_id: str,
    updates: dict[str, Any],
    *,
    week_start: Optional[date] = None,
    effective_date: Optional[date] = None,
) -> dict[str, Any]:
    mutation_week_start, _ = _resolve_mutation_calendar(
        week_start=week_start, effective_date=effective_date
    )
    lock_user_schedule(db, user_id)
    profile = get_onboarding_by_user_id(db, user_id)
    current = dict(profile.data) if profile else {}
    if not updates:
        return _canonical_preferences(current)

    _validate_equipment_ids(db, updates.get("selectedEquipment"))
    current.update(updates)
    _validate_merged_preferences({**current, **_canonical_preferences(current)})

    if profile and current == profile.data:
        return _canonical_preferences(current)

    upsert_onboarding(db, user_id, current, commit=False)
    active_programs = (
        db.query(UserProgramActivation)
        .filter(
            UserProgramActivation.user_id == user_id,
            UserProgramActivation.status.in_(["active", "scheduled"]),
        )
        .all()
    )
    current_programs = [item for item in active_programs if item.status == "active"]
    scheduled_programs = [item for item in active_programs if item.status == "scheduled"]
    if "selectedEquipment" in updates:
        for active_program in active_programs:
            active_program.requires_review = True
    if not current_programs:
        scheduled_week_start = min(
            (
                item.effective_date - timedelta(days=item.effective_date.weekday())
                for item in scheduled_programs
            ),
            default=None,
        )
        _invalidate_uncompleted_current_and_future_plans(
            db,
            user_id,
            week_start=mutation_week_start,
            before_week_start=scheduled_week_start,
        )
    db.commit()
    return _canonical_preferences(current)


def _canonical_preferences(data: dict[str, Any]) -> dict[str, Any]:
    preferences: dict[str, Any] = {}
    for field in PREFERENCE_FIELDS:
        value = data.get(field)
        if value in (None, "", [], {}):
            value = next(
                (
                    data[key]
                    for key in _LEGACY_FIELDS[field]
                    if data.get(key) not in (None, "", [], {})
                ),
                None,
            )
        canonical = _canonical_preference_value(field, value)
        if canonical is not None:
            preferences[field] = canonical
    return preferences


def _canonical_preference_value(field: str, value: Any) -> Any:
    if field == "workoutFrequency":
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return _FREQUENCY_VALUES.get(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in _FREQUENCY_VALUES.values():
                return normalized
            try:
                return _FREQUENCY_VALUES.get(int(normalized))
            except ValueError:
                return None
        return None
    if field == "fitnessGoal":
        return _GOAL_VALUES.get(value.strip().lower()) if isinstance(value, str) else None
    if field == "experienceLevel":
        return _EXPERIENCE_VALUES.get(value.strip().lower()) if isinstance(value, str) else None
    if field == "workoutSplit":
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        return _SPLIT_VALUES.get(normalized, normalized) if normalized in {
            "ai-optimized", "push-pull-legs", "custom", "upper-lower",
            "push-pull-legs-full-body", "push-pull-legs-upper-lower",
            "upper-lower-full-body", "full-body", "bro-split",
            "lower-focused-upper", "push-pull-legs-upper-body", * _SPLIT_VALUES,
        } else None
    if field == "trainingPlace":
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        return _PLACE_VALUES.get(normalized, normalized) if normalized in {
            "large-gym", "small-gym", "garage-gym", *_PLACE_VALUES,
        } else None
    if field == "selectedEquipment":
        return value if isinstance(value, list) else None
    if field == "customWorkouts":
        if not isinstance(value, list):
            return None
        workouts = []
        for workout in value:
            if not isinstance(workout, dict):
                return None
            muscles = workout.get("muscleGroups", workout.get("muscle_groups"))
            if not all(isinstance(workout.get(key), str) for key in ("id", "name", "type")) or not isinstance(muscles, list):
                return None
            workouts.append({
                "id": workout["id"],
                "name": workout["name"],
                "type": workout["type"],
                "muscleGroups": muscles,
            })
        return workouts
    return None


def _invalidate_uncompleted_current_and_future_plans(
    db: Session,
    user_id: str,
    *,
    week_start: date,
    before_week_start: Optional[date] = None,
) -> None:
    """Refresh uncompleted plans while retaining completed current-week snapshots."""
    plans = (
        db.query(WorkoutWeekPlan)
        .filter(
            WorkoutWeekPlan.user_id == user_id,
            WorkoutWeekPlan.week_start_date >= week_start,
        )
        .all()
    )
    plan_workout_day_ids = {
        workout.get("workoutDayId")
        for plan in plans
        for workout in plan.plan_json.get("workouts", [])
        if isinstance(workout.get("workoutDayId"), str)
    }
    completed_day_ids = {
        row[0]
        for row in (
            db.query(WorkoutSession.workout_day_id)
            .filter(
                WorkoutSession.user_id == user_id,
                WorkoutSession.workout_day_id.in_(plan_workout_day_ids),
                WorkoutSession.status == "completed",
            )
            .all()
        )
    }
    for plan in plans:
        if before_week_start is not None and plan.week_start_date >= before_week_start:
            continue
        if plan.plan_json.get("programActivationId"):
            continue
        if plan.week_start_date > week_start:
            db.delete(plan)
            continue

        plan.plan_json = {
            **plan.plan_json,
            "preferencesRefreshRequired": True,
            "completedWorkoutSnapshots": list({
                workout["workoutDayId"]: workout
                for workout in (
                    plan.plan_json.get("completedWorkoutSnapshots", [])
                    + plan.plan_json.get("workouts", [])
                )
                if isinstance(workout.get("workoutDayId"), str)
                and workout["workoutDayId"] in completed_day_ids
            }.values()),
        }


def _resolve_mutation_calendar(
    *, week_start: Optional[date], effective_date: Optional[date]
) -> tuple[date, date]:
    if (week_start is None) != (effective_date is None):
        raise ValueError("weekStart and effectiveDate must be supplied together")
    server_today = datetime.now(timezone.utc).date()
    if effective_date is None:
        effective_date = server_today
        week_start = effective_date - timedelta(days=effective_date.weekday())
        return week_start, effective_date
    if effective_date not in {
        server_today - timedelta(days=1),
        server_today,
        server_today + timedelta(days=1),
    }:
        raise ValueError("effectiveDate must be the caller's current local date")
    normalized_week_start = week_start - timedelta(days=week_start.weekday())
    effective_week_start = effective_date - timedelta(days=effective_date.weekday())
    if normalized_week_start != effective_week_start:
        raise ValueError("weekStart must contain effectiveDate")
    return normalized_week_start, effective_date


def _validate_equipment_ids(db: Session, equipment_ids: Any) -> None:
    if equipment_ids is None:
        return
    ids = set(equipment_ids)
    known_ids = {
        row[0]
        for row in db.query(Equipment.id).filter(Equipment.id.in_(ids)).all()
    }
    unknown_ids = ids - known_ids
    if unknown_ids:
        raise ValueError("Unknown equipment: " + ", ".join(sorted(unknown_ids)))


def _validate_merged_preferences(preferences: dict[str, Any]) -> None:
    if preferences.get("workoutSplit") == "custom" and not preferences.get("customWorkouts"):
        raise ValueError("a custom split requires at least one workout")
