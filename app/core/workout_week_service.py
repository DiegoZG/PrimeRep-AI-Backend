"""Weekly workout plan service: persisted weekly plans with skip/duration/regen."""

from datetime import date, datetime, timedelta
import random
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.coach_service import generate_coach_content, generate_week_coach_content
from app.core.progression_service import suggest_weight_kg
from app.core.workout_service import (
    LOWER_MUSCLES,
    MAIN_EXERCISE_TYPES,
    ACCESSORY_EXERCISE_TYPES,
    MAIN_PRESCRIPTION,
    ACCESSORY_PRESCRIPTION,
    get_onboarding_settings,
    get_eligible_exercises,
    _filter_by_day_type,
    _pick_exercises,
    _exercise_to_schema,
    OnboardingWorkoutSettings,
    WorkoutDayDefinition,
)
from app.models.user import UserDailyForceRegen
from app.models.workout_session import WorkoutSession
from app.models.workout_week_plan import WorkoutWeekPlan
from app.schemas.workout import (
    WorkoutBlockItemOut,
    WorkoutExerciseBlockOut,
    WorkoutPrescriptionOut,
)
from app.schemas.workout_week import WorkoutDayOut, WorkoutWeekResponseOut


# Day templates: which days of week to schedule workouts (0=Monday, 6=Sunday)
DAYS_PER_WEEK_TEMPLATES = {
    1: [2],              # Wed
    2: [0, 3],           # Mon, Thu
    3: [0, 2, 4],        # Mon, Wed, Fri
    4: [0, 1, 3, 5],     # Mon, Tue, Thu, Sat
    5: [0, 1, 2, 4, 5],  # Mon, Tue, Wed, Fri, Sat
    6: [0, 1, 2, 3, 4, 5],
    7: [0, 1, 2, 3, 4, 5, 6],
}

# Duration → exercise count mapping
# Returns (main_count, accessory_count)
DURATION_EXERCISE_COUNTS = {
    25: (2, 1),   # ≤25 min → 3 exercises (2 main, 1 accessory)
    40: (2, 2),   # 30-40 min → 4 exercises (2 main, 2 accessory)
    55: (3, 3),   # 45-55 min → 6 exercises (3 main, 3 accessory)
    120: (3, 4),  # 60+ min → 7 exercises (3 main, 4 accessory)
}

DEFAULT_DURATION_MINUTES = 35
DAILY_FORCE_REGEN_LIMIT = 3
CYCLE_EPOCH_WEEK_START = date(2020, 1, 6)
ONBOARDING_WORKOUT_CONTRACT_VERSION = 1

FULL_BODY_FOUNDATION_ROLES = {"lower", "push", "pull"}
DIRECT_ISOLATION_MUSCLES = {"abs", "biceps", "triceps", "calves", "forearms"}


def _check_and_increment_force_regen(db: Session, user_id: str) -> None:
    """Increment today's force-regen counter and raise ValueError if limit exceeded.

    Callers should catch ValueError and return HTTP 429.
    """
    today = date.today()
    row = (
        db.query(UserDailyForceRegen)
        .filter_by(user_id=user_id, regen_date=today)
        .first()
    )
    if row and row.count >= DAILY_FORCE_REGEN_LIMIT:
        raise ValueError(
            f"Force regeneration limit reached ({DAILY_FORCE_REGEN_LIMIT}/day). "
            "Try again tomorrow."
        )
    if row:
        row.count += 1
    else:
        db.add(UserDailyForceRegen(user_id=user_id, regen_date=today, count=1))
    db.commit()


def _get_exercise_counts(duration_minutes: int) -> tuple[int, int]:
    """Get (main_count, accessory_count) based on duration."""
    for threshold, counts in sorted(DURATION_EXERCISE_COUNTS.items()):
        if duration_minutes <= threshold:
            return counts
    # Fallback for very long durations
    return (3, 4)


def _full_body_role(exercise) -> str:
    if exercise.primary_muscle in {"quads", "hamstrings", "glutes"}:
        return "lower"
    if exercise.primary_muscle in {"chest", "shoulders"}:
        return "push"
    if exercise.primary_muscle == "back":
        return "pull"
    return "isolation"


def _pick_balanced_full_body_exercises(
    *,
    pool: list,
    count: int,
    preferred_types: set[str],
    selected: list,
    tie_breakers: dict[str, float],
) -> list:
    """Select complementary exercises without stacking direct isolation work."""
    picked: list = []

    while len(picked) < count:
        already_selected = selected + picked
        selected_ids = {exercise.id for exercise in already_selected}
        selected_primary_muscles = {
            exercise.primary_muscle for exercise in already_selected
        }
        selected_roles = {
            _full_body_role(exercise) for exercise in already_selected
        }
        available = [exercise for exercise in pool if exercise.id not in selected_ids]
        if not available:
            break

        def score(exercise) -> int:
            role = _full_body_role(exercise)
            primary_muscle = exercise.primary_muscle
            value = 0

            if role in FULL_BODY_FOUNDATION_ROLES and role not in selected_roles:
                value += 1_000
            if primary_muscle in DIRECT_ISOLATION_MUSCLES:
                if primary_muscle in selected_primary_muscles:
                    return -10_000
                if FULL_BODY_FOUNDATION_ROLES - selected_roles:
                    value -= 500
                else:
                    value -= 20
            if primary_muscle in selected_primary_muscles:
                value -= 60
            if exercise.exercise_type in preferred_types:
                value += 25
            return value

        chosen = min(
            available,
            key=lambda exercise: (
                -score(exercise),
                tie_breakers[exercise.id],
                exercise.id,
            ),
        )
        if score(chosen) <= -10_000:
            break
        picked.append(chosen)

    return picked


def _pick_custom_coverage_exercises(
    *,
    rng: random.Random,
    pool: list,
    requested_muscles: tuple[str, ...],
    main_count: int,
    accessory_count: int,
    rotation_offset: int,
) -> tuple[list, list, list[str], list[str]]:
    """Cover every requested custom muscle before filling remaining slots."""
    total_count = main_count + accessory_count
    if not requested_muscles:
        return [], [], [], []

    offset = rotation_offset % len(requested_muscles)
    rotated = requested_muscles[offset:] + requested_muscles[:offset]
    candidates = {
        muscle: [exercise for exercise in pool if exercise.primary_muscle == muscle]
        for muscle in requested_muscles
    }
    unavailable = [muscle for muscle in requested_muscles if not candidates[muscle]]
    available = [muscle for muscle in rotated if candidates[muscle]]
    coverage_muscles = available[:total_count]
    deferred = [
        muscle for muscle in requested_muscles
        if muscle not in coverage_muscles and muscle not in unavailable
    ]

    selected: list = []
    for index, muscle in enumerate(coverage_muscles):
        preferred_types = MAIN_EXERCISE_TYPES if index < main_count else ACCESSORY_EXERCISE_TYPES
        choices = [
            exercise for exercise in candidates[muscle]
            if exercise.id not in {item.id for item in selected}
        ]
        preferred = [exercise for exercise in choices if exercise.exercise_type in preferred_types]
        fallback = [exercise for exercise in choices if exercise.exercise_type not in preferred_types]
        rng.shuffle(preferred)
        rng.shuffle(fallback)
        if preferred or fallback:
            selected.append((preferred or fallback)[0])

    main_exercises = selected[:main_count]
    selected_ids = {exercise.id for exercise in main_exercises}
    while len(main_exercises) < main_count:
        addition = _pick_exercises(rng, pool, 1, MAIN_EXERCISE_TYPES, selected_ids)
        if not addition:
            break
        main_exercises.extend(addition)
        selected_ids.add(addition[0].id)

    accessory_exercises = selected[main_count:]
    selected_ids.update(exercise.id for exercise in accessory_exercises)
    while len(accessory_exercises) < accessory_count:
        addition = _pick_exercises(rng, pool, 1, ACCESSORY_EXERCISE_TYPES, selected_ids)
        if not addition:
            break
        accessory_exercises.extend(addition)
        selected_ids.add(addition[0].id)

    return main_exercises, accessory_exercises, deferred, unavailable


def get_week_start(target_date: date) -> date:
    """Get the Monday of the week containing target_date."""
    return target_date - timedelta(days=target_date.weekday())


def _get_template_dates(week_start: date, days_per_week: int) -> list[date]:
    """Get the workout dates for a week based on days_per_week template."""
    capped_days = max(1, min(7, days_per_week))
    day_offsets = DAYS_PER_WEEK_TEMPLATES.get(capped_days, DAYS_PER_WEEK_TEMPLATES[4])
    return [week_start + timedelta(days=offset) for offset in day_offsets]


def _get_cycle_start_index(
    week_start: date,
    days_per_week: int,
    cycle_length: int,
) -> int:
    week_number = (get_week_start(week_start) - CYCLE_EPOCH_WEEK_START).days // 7
    return (week_number * days_per_week) % cycle_length


def _prescription_with_suggestion(
    db: Optional[Session],
    *,
    user_id: Optional[str],
    exercise,
    base: WorkoutPrescriptionOut,
) -> WorkoutPrescriptionOut:
    """Copy a static prescription and stamp on a suggested load when history exists."""
    if not user_id or db is None:
        return base

    suggestion = suggest_weight_kg(
        db,
        user_id=user_id,
        exercise_id=exercise.id,
        is_lower_body=exercise.primary_muscle in LOWER_MUSCLES,
        reps_min=base.reps_min,
        reps_max=base.reps_max,
    )
    if suggestion is None:
        return base
    return base.model_copy(
        update={
            "suggested_weight_kg": suggestion.weight_kg,
            "suggested_weight_reason": suggestion.reason,
        }
    )


def _generate_single_workout(
    *,
    rng: random.Random,
    all_eligible: list,
    day_type: str,
    split_preference: str,
    day_definition: WorkoutDayDefinition,
    duration_minutes: int,
    workout_date: date,
    slot_index: int,
    workout_day_id: Optional[str] = None,
    db: Optional[Session] = None,
    user_id: Optional[str] = None,
) -> tuple[dict[str, Any], list[tuple]]:
    """Generate a single workout day entry.

    Returns:
        (workout_dict, all_items) where workout_dict matches WorkoutDayOut
        schema structure (without coach content) and all_items is a list of
        (exercise_orm, WorkoutPrescriptionOut) for the batch coach call.
    """
    if workout_day_id is None:
        workout_day_id = str(uuid.uuid4())

    day_pool = _filter_by_day_type(all_eligible, day_type, day_definition.muscles)

    main_count, accessory_count = _get_exercise_counts(duration_minutes)
    selected_ids: set[str] = set()
    deferred_muscles: list[str] = []
    unavailable_muscles: list[str] = []
    if day_definition.restrict_to_muscles:
        main_exercises, accessory_exercises, deferred_muscles, unavailable_muscles = _pick_custom_coverage_exercises(
            rng=rng,
            pool=day_pool,
            requested_muscles=day_definition.ordered_muscles,
            main_count=main_count,
            accessory_count=accessory_count,
            rotation_offset=workout_date.toordinal() // 7,
        )
    elif day_type == "full_body":
        tie_breakers = {exercise.id: rng.random() for exercise in day_pool}
        main_exercises = _pick_balanced_full_body_exercises(
            pool=day_pool,
            count=main_count,
            preferred_types=MAIN_EXERCISE_TYPES,
            selected=[],
            tie_breakers=tie_breakers,
        )
        selected_ids.update(exercise.id for exercise in main_exercises)
        accessory_exercises = _pick_balanced_full_body_exercises(
            pool=day_pool,
            count=accessory_count,
            preferred_types=ACCESSORY_EXERCISE_TYPES,
            selected=main_exercises,
            tie_breakers=tie_breakers,
        )
    else:
        main_exercises = _pick_exercises(
            rng,
            day_pool,
            count=main_count,
            preferred_types=MAIN_EXERCISE_TYPES,
            exclude_ids=selected_ids,
        )
        selected_ids.update(exercise.id for exercise in main_exercises)
        accessory_exercises = _pick_exercises(
            rng,
            day_pool,
            count=accessory_count,
            preferred_types=ACCESSORY_EXERCISE_TYPES,
            exclude_ids=selected_ids,
        )

    main_items: list[tuple] = [
        (ex, _prescription_with_suggestion(db, user_id=user_id, exercise=ex, base=MAIN_PRESCRIPTION))
        for ex in main_exercises
    ]
    accessory_items: list[tuple] = [
        (ex, _prescription_with_suggestion(db, user_id=user_id, exercise=ex, base=ACCESSORY_PRESCRIPTION))
        for ex in accessory_exercises
    ]
    all_items = main_items + accessory_items
    title = day_definition.title

    main_block = WorkoutExerciseBlockOut(
        block_type="main",
        items=[
            WorkoutBlockItemOut(exercise=_exercise_to_schema(ex), prescription=pres)
            for ex, pres in main_items
        ],
    )
    accessory_block = WorkoutExerciseBlockOut(
        block_type="accessory",
        items=[
            WorkoutBlockItemOut(exercise=_exercise_to_schema(ex), prescription=pres)
            for ex, pres in accessory_items
        ],
    )

    workout_dict = {
        "workoutDayId": workout_day_id,
        "slotIndex": slot_index,
        "date": workout_date.isoformat(),
        "durationMinutes": duration_minutes,
        "title": title,
        "splitKey": split_preference,
        "dayType": day_type,
        "estimatedMinutes": duration_minutes,
        "workoutIntent": None,
        "deferredMuscles": deferred_muscles,
        "unavailableMuscles": unavailable_muscles,
        "exerciseBlocks": [
            main_block.model_dump(by_alias=True),
            accessory_block.model_dump(by_alias=True),
        ],
    }
    return workout_dict, all_items


def _stamp_coach_content(workout_dict: dict[str, Any], coach) -> None:
    """Stamp workoutIntent and exerciseRationale from coach onto a workout dict in-place."""
    workout_dict["workoutIntent"] = coach.workout_intent
    for block in workout_dict.get("exerciseBlocks", []):
        for item in block.get("items", []):
            ex_id = item.get("exercise", {}).get("id")
            if ex_id and ex_id in coach.exercise_rationale:
                item["exerciseRationale"] = coach.exercise_rationale[ex_id]


def _generate_week_plan(
    db: Session,
    *,
    user_id: str,
    week_start: date,
    owned_equipment_ids: set[str],
    split_preference: str,
    day_cycle: tuple[WorkoutDayDefinition, ...],
    days_per_week: int,
    personalization_seed: str = "",
    existing_durations: Optional[dict[int, int]] = None,
    existing_workout_day_ids: Optional[dict[int, str]] = None,
    force_new_ids: bool = False,
    skip_week_coach: bool = False,
) -> tuple[dict[str, Any], dict[str, list[tuple]]]:
    """
    Generate a complete week plan.

    Args:
        existing_durations: Dict of slotIndex -> durationMinutes to preserve
        existing_workout_day_ids: Dict of slotIndex -> workoutDayId to preserve (ignored if force_new_ids)
        force_new_ids: If True, generate new workoutDayIds for all entries
        skip_week_coach: If True, skip the batched week coach call (caller handles coaching)

    Returns:
        (plan_json, coach_items_by_workout_id) where coach_items_by_workout_id maps
        workoutDayId -> exercise_items tuples for single-day coach calls.
    """
    capped_days = max(1, min(7, days_per_week))
    template_dates = _get_template_dates(week_start, capped_days)

    # Determine seed
    if force_new_ids:
        seed_str = f"{user_id}:{week_start.isoformat()}:{personalization_seed}:{uuid.uuid4()}"
    else:
        seed_str = f"{user_id}:{week_start.isoformat()}:{personalization_seed}"
    rng = random.Random(seed_str)

    # Fetch eligible exercises once for the entire week
    all_eligible = get_eligible_exercises(db, owned_equipment_ids=owned_equipment_ids)

    workouts: list[dict[str, Any]] = []
    coach_days_input: list[dict] = []
    coach_items_by_id: dict[str, list[tuple]] = {}
    cycle_start_index = _get_cycle_start_index(
        week_start,
        capped_days,
        len(day_cycle),
    )
    for slot_index, workout_date in enumerate(template_dates):
        cycle_index = (cycle_start_index + slot_index) % len(day_cycle)
        day_definition = day_cycle[cycle_index]
        day_type = day_definition.day_type

        # Get duration (preserved or default)
        duration = DEFAULT_DURATION_MINUTES
        if existing_durations and slot_index in existing_durations:
            duration = existing_durations[slot_index]

        # Get workout_day_id (preserved or new)
        workout_day_id = None
        if not force_new_ids and existing_workout_day_ids and slot_index in existing_workout_day_ids:
            workout_day_id = existing_workout_day_ids[slot_index]

        workout_dict, all_items = _generate_single_workout(
            rng=rng,
            all_eligible=all_eligible,
            day_type=day_type,
            split_preference=split_preference,
            day_definition=day_definition,
            duration_minutes=duration,
            workout_date=workout_date,
            slot_index=slot_index,
            workout_day_id=workout_day_id,
            db=db,
            user_id=user_id,
        )
        workout_dict["cycleIndex"] = cycle_index
        workouts.append(workout_dict)
        coach_items_by_id[workout_dict["workoutDayId"]] = all_items
        coach_days_input.append({
            "workoutDayId": workout_dict["workoutDayId"],
            "day_type": day_type,
            "title": workout_dict["title"],
            "exercise_items": all_items,
        })

    if not skip_week_coach:
        # One batched Anthropic call for the whole week (never raises)
        week_coach = generate_week_coach_content(db, user_id=user_id, days=coach_days_input)
        for w in workouts:
            day_coach = week_coach.days.get(w["workoutDayId"])
            if day_coach:
                _stamp_coach_content(w, day_coach)

    plan_json = {
        "weekStart": week_start.isoformat(),
        "daysPerWeek": capped_days,
        "onboardingWorkoutContractVersion": ONBOARDING_WORKOUT_CONTRACT_VERSION,
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "seed": seed_str,
        "workouts": workouts,
    }
    return plan_json, coach_items_by_id


def get_week_plan(
    db: Session,
    *,
    user_id: str,
    week_start: Optional[date] = None,
) -> Optional[WorkoutWeekPlan]:
    """Get existing week plan from DB, or None if not found."""
    if week_start is None:
        week_start = get_week_start(date.today())

    return (
        db.query(WorkoutWeekPlan)
        .filter(
            WorkoutWeekPlan.user_id == user_id,
            WorkoutWeekPlan.week_start_date == week_start,
        )
        .first()
    )


def get_or_create_week_plan(
    db: Session,
    *,
    user_id: str,
    week_start: Optional[date] = None,
    force_regenerate: bool = False,
) -> WorkoutWeekResponseOut:
    """
    Get existing week plan or create a new one.
    
    Args:
        force_regenerate: If True, regenerate the plan with new workoutDayIds
                         (but preserve per-slot durations from existing plan).
    """
    if week_start is None:
        week_start = get_week_start(date.today())

    # Load onboarding settings
    settings = get_onboarding_settings(db, user_id=user_id)
    split_preference = settings.split_preference
    days_per_week = settings.days_per_week
    owned_equipment_ids = _get_owned_equipment_ids_from_settings(db, settings)

    existing_plan = get_week_plan(db, user_id=user_id, week_start=week_start)

    preferences_refresh_required = bool(
        existing_plan
        and existing_plan.plan_json.get("preferencesRefreshRequired")
    )
    contract_is_current = bool(
        existing_plan
        and existing_plan.plan_json.get("onboardingWorkoutContractVersion")
        == ONBOARDING_WORKOUT_CONTRACT_VERSION
    )
    if (
        existing_plan
        and not force_regenerate
        and contract_is_current
        and not preferences_refresh_required
    ):
        # Return existing plan
        return _plan_json_to_response(existing_plan.plan_json)

    # Extract existing durations to preserve (by slotIndex)
    existing_durations: Optional[dict[int, int]] = None
    existing_workout_day_ids: Optional[dict[int, str]] = None
    if existing_plan:
        existing_durations = {}
        existing_workout_day_ids = {}
        for w in existing_plan.plan_json.get("workouts", []):
            slot_idx = w.get("slotIndex", 0)
            existing_durations[slot_idx] = w.get("durationMinutes", DEFAULT_DURATION_MINUTES)
            workout_day_id = w.get("workoutDayId")
            if isinstance(workout_day_id, str):
                existing_workout_day_ids[slot_idx] = workout_day_id

    completed_snapshots: dict[str, dict[str, Any]] = {}
    if existing_plan and preferences_refresh_required:
        completed_ids = {
            row[0]
            for row in (
                db.query(WorkoutSession.workout_day_id)
                .filter(
                    WorkoutSession.user_id == user_id,
                    WorkoutSession.completed_at.isnot(None),
                )
                .all()
            )
        }
        completed_snapshots = {
            workout_day_id: workout
            for workout in (
                existing_plan.plan_json.get("completedWorkoutSnapshots", [])
                + existing_plan.plan_json.get("workouts", [])
            )
            if isinstance((workout_day_id := workout.get("workoutDayId")), str)
            and workout_day_id in completed_ids
        }
        # A slot describes a position in a particular schedule, not a durable
        # workout identity. Reusing it after a frequency change can attach a
        # completed Monday workout to a newly scheduled Wednesday (or vice
        # versa). Historical snapshots remain in the plan metadata instead.
        existing_workout_day_ids = None

    # Generate new plan
    plan_json, _ = _generate_week_plan(
        db,
        user_id=user_id,
        week_start=week_start,
        owned_equipment_ids=owned_equipment_ids,
        split_preference=split_preference,
        day_cycle=settings.day_cycle,
        days_per_week=days_per_week,
        personalization_seed=settings.selection_seed,
        existing_durations=existing_durations,
        existing_workout_day_ids=(
            None if force_regenerate else existing_workout_day_ids
        ),
        force_new_ids=force_regenerate,
    )

    if completed_snapshots:
        plan_json["completedWorkoutSnapshots"] = list(completed_snapshots.values())

    # Upsert the plan
    if existing_plan:
        existing_plan.plan_json = plan_json
        existing_plan.days_per_week = days_per_week
        flag_modified(existing_plan, "plan_json")
        db.add(existing_plan)
        db.commit()
        db.refresh(existing_plan)
        return _plan_json_to_response(existing_plan.plan_json)
    else:
        new_plan = WorkoutWeekPlan(
            id=str(uuid.uuid4()),
            user_id=user_id,
            week_start_date=week_start,
            days_per_week=days_per_week,
            plan_json=plan_json,
        )
        db.add(new_plan)
        db.commit()
        db.refresh(new_plan)
        return _plan_json_to_response(new_plan.plan_json)


def skip_workout_day(
    db: Session,
    *,
    user_id: str,
    workout_day_id: str,
    week_start: Optional[date] = None,
) -> Optional[WorkoutWeekResponseOut]:
    """
    Skip a workout day: remove it and append a new one at the end.
    
    Returns None if the workout_day_id is not found in the current plan.
    """
    if week_start is None:
        week_start = get_week_start(date.today())

    plan = get_week_plan(db, user_id=user_id, week_start=week_start)
    if not plan:
        return None

    workouts = plan.plan_json.get("workouts", [])

    # Find and remove the workout
    removed_idx = None
    for i, w in enumerate(workouts):
        if w.get("workoutDayId") == workout_day_id:
            removed_idx = i
            break

    if removed_idx is None:
        # workoutDayId not found
        return None

    removed_workout = workouts.pop(removed_idx)

    # Load settings
    settings = get_onboarding_settings(db, user_id=user_id)
    split_preference = settings.split_preference
    days_per_week = settings.days_per_week
    owned_equipment_ids = _get_owned_equipment_ids_from_settings(db, settings)

    # Re-assign slotIndex and dates
    capped_days = max(1, min(7, days_per_week))
    template_dates = _get_template_dates(week_start, capped_days)
    cycle_start_index = _get_cycle_start_index(
        week_start,
        capped_days,
        len(settings.day_cycle),
    )

    def get_stored_cycle_index(workout: dict[str, Any], fallback_slot: int) -> int:
        stored_index = workout.get("cycleIndex")
        if isinstance(stored_index, int):
            return stored_index % len(settings.day_cycle)
        stored_slot = workout.get("slotIndex", fallback_slot)
        if not isinstance(stored_slot, int):
            stored_slot = fallback_slot
        return (cycle_start_index + stored_slot) % len(settings.day_cycle)

    removed_cycle_index = get_stored_cycle_index(removed_workout, removed_idx)
    for position, workout in enumerate(workouts):
        workout["cycleIndex"] = get_stored_cycle_index(workout, position)

    for i, w in enumerate(workouts):
        w["slotIndex"] = i
        if i < len(template_dates):
            w["date"] = template_dates[i].isoformat()

    previous_cycle_index = (
        workouts[-1]["cycleIndex"] if workouts else removed_cycle_index
    )
    new_cycle_index = (previous_cycle_index + 1) % len(settings.day_cycle)
    new_day = settings.day_cycle[new_cycle_index]
    new_day_type = new_day.day_type

    # Generate the new workout
    new_slot_index = len(workouts)
    new_date = template_dates[new_slot_index] if new_slot_index < len(template_dates) else template_dates[-1]

    # Use a random seed for the new workout
    rng = random.Random(f"{user_id}:{week_start.isoformat()}:{uuid.uuid4()}")
    all_eligible = get_eligible_exercises(db, owned_equipment_ids=owned_equipment_ids)

    new_workout, new_items = _generate_single_workout(
        rng=rng,
        all_eligible=all_eligible,
        day_type=new_day_type,
        split_preference=split_preference,
        day_definition=new_day,
        duration_minutes=DEFAULT_DURATION_MINUTES,
        workout_date=new_date,
        slot_index=new_slot_index,
        workout_day_id=None,
        db=db,
        user_id=user_id,
    )
    new_workout["cycleIndex"] = new_cycle_index

    # Single-day coach call for the replacement workout
    new_title = new_day.title
    coach = generate_coach_content(
        db,
        user_id=user_id,
        day_type=new_day_type,
        title=new_title,
        exercise_items=new_items,
    )
    _stamp_coach_content(new_workout, coach)

    workouts.append(new_workout)

    # Update plan_json with new workouts, generatedAt, and seed
    new_seed = f"{user_id}:{week_start.isoformat()}:skip:{uuid.uuid4()}"
    plan.plan_json = {
        **plan.plan_json,
        "workouts": workouts,
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "seed": new_seed,
    }
    flag_modified(plan, "plan_json")
    db.add(plan)
    db.commit()
    db.refresh(plan)

    return _plan_json_to_response(plan.plan_json)


def update_workout_duration(
    db: Session,
    *,
    user_id: str,
    workout_day_id: str,
    duration_minutes: int,
    week_start: Optional[date] = None,
) -> Optional[WorkoutWeekResponseOut]:
    """
    Update duration for a workout day and regenerate the week (preserving IDs and all durations).
    
    Returns None if the workout_day_id is not found in the current plan.
    """
    if week_start is None:
        week_start = get_week_start(date.today())

    plan = get_week_plan(db, user_id=user_id, week_start=week_start)
    if not plan:
        return None

    workouts = plan.plan_json.get("workouts", [])

    # Find the workout and update its duration
    found = False
    for w in workouts:
        if w.get("workoutDayId") == workout_day_id:
            w["durationMinutes"] = duration_minutes
            found = True
            break

    if not found:
        return None

    # Snapshot existing coach content by workoutDayId before regeneration
    old_coach: dict[str, dict] = {
        w["workoutDayId"]: {
            "workoutIntent": w.get("workoutIntent"),
            "exerciseRationale": {
                item["exercise"]["id"]: item.get("exerciseRationale")
                for block in w.get("exerciseBlocks", [])
                for item in block.get("items", [])
                if item.get("exercise", {}).get("id")
            },
        }
        for w in workouts
        if w.get("workoutDayId")
    }

    # Extract durations and IDs by slotIndex
    existing_durations: dict[int, int] = {}
    existing_workout_day_ids: dict[int, str] = {}
    for w in workouts:
        slot_idx = w.get("slotIndex", 0)
        existing_durations[slot_idx] = w.get("durationMinutes", DEFAULT_DURATION_MINUTES)
        existing_workout_day_ids[slot_idx] = w.get("workoutDayId")

    # Load settings
    settings = get_onboarding_settings(db, user_id=user_id)
    split_preference = settings.split_preference
    days_per_week = settings.days_per_week
    owned_equipment_ids = _get_owned_equipment_ids_from_settings(db, settings)

    # Regenerate week with preserved IDs and durations (no batched coach call)
    new_plan_json, coach_items_by_id = _generate_week_plan(
        db,
        user_id=user_id,
        week_start=week_start,
        owned_equipment_ids=owned_equipment_ids,
        split_preference=split_preference,
        day_cycle=settings.day_cycle,
        days_per_week=days_per_week,
        personalization_seed=settings.selection_seed,
        existing_durations=existing_durations,
        existing_workout_day_ids=existing_workout_day_ids,
        force_new_ids=False,
        skip_week_coach=True,
    )

    # Restore cached coach content for unchanged days only
    for w in new_plan_json.get("workouts", []):
        day_id = w.get("workoutDayId")
        if day_id == workout_day_id:
            continue
        cached = old_coach.get(day_id or "")
        if cached and cached.get("workoutIntent"):
            w["workoutIntent"] = cached["workoutIntent"]
            for block in w.get("exerciseBlocks", []):
                for item in block.get("items", []):
                    ex_id = item.get("exercise", {}).get("id")
                    stored = cached["exerciseRationale"].get(ex_id) if ex_id else None
                    if stored:
                        item["exerciseRationale"] = stored

    # Single-day coach call for the duration-changed workout only
    changed_workout = next(
        (w for w in new_plan_json.get("workouts", []) if w.get("workoutDayId") == workout_day_id),
        None,
    )
    if changed_workout:
        coach = generate_coach_content(
            db,
            user_id=user_id,
            day_type=changed_workout["dayType"],
            title=changed_workout["title"],
            exercise_items=coach_items_by_id.get(workout_day_id, []),
        )
        _stamp_coach_content(changed_workout, coach)

    plan.plan_json = new_plan_json
    flag_modified(plan, "plan_json")
    db.add(plan)
    db.commit()
    db.refresh(plan)

    return _plan_json_to_response(plan.plan_json)


def select_next_scheduled_workout(
    plan_json: dict[str, Any],
    *,
    target_date: Optional[date] = None,
) -> Optional[dict[str, Any]]:
    """
    Select the next scheduled workout from the plan.
    Returns the workout with the smallest date >= target_date.
    """
    if target_date is None:
        target_date = date.today()

    workouts = plan_json.get("workouts", [])
    if not workouts:
        return None

    # Find workouts with date >= target_date
    upcoming = []
    for w in workouts:
        w_date = date.fromisoformat(w["date"])
        if w_date >= target_date:
            upcoming.append((w_date, w.get("slotIndex", 0), w))

    if not upcoming:
        return None

    # Sort by date, then slotIndex
    upcoming.sort(key=lambda x: (x[0], x[1]))
    return upcoming[0][2]


def _get_owned_equipment_ids_from_settings(
    db: Session, settings: OnboardingWorkoutSettings
) -> set[str]:
    """Extract and validate equipment IDs from onboarding settings."""
    from app.models.equipment import Equipment

    equipment_slugs = settings.equipment_ids
    if not equipment_slugs:
        return set()

    valid_ids = (
        db.query(Equipment.id)
        .filter(Equipment.id.in_(equipment_slugs))
        .all()
    )
    return {row[0] for row in valid_ids}


def _plan_json_to_response(plan_json: dict[str, Any]) -> WorkoutWeekResponseOut:
    """Convert stored plan_json to WorkoutWeekResponseOut schema."""
    workouts = []
    for w in plan_json.get("workouts", []):
        # Convert exercise blocks from dict to schema
        exercise_blocks = []
        for block_dict in w.get("exerciseBlocks", []):
            block = WorkoutExerciseBlockOut.model_validate(block_dict)
            exercise_blocks.append(block)

        workout = WorkoutDayOut(
            workout_day_id=w["workoutDayId"],
            slot_index=w["slotIndex"],
            date=date.fromisoformat(w["date"]),
            duration_minutes=w["durationMinutes"],
            title=w["title"],
            split_key=w["splitKey"],
            day_type=w["dayType"],
            estimated_minutes=w["estimatedMinutes"],
            workout_intent=w.get("workoutIntent"),
            deferred_muscles=w.get("deferredMuscles", []),
            unavailable_muscles=w.get("unavailableMuscles", []),
            exercise_blocks=exercise_blocks,
        )
        workouts.append(workout)

    return WorkoutWeekResponseOut(
        week_start=date.fromisoformat(plan_json["weekStart"]),
        days_per_week=plan_json["daysPerWeek"],
        generated_at=datetime.fromisoformat(plan_json["generatedAt"].rstrip("Z")),
        seed=plan_json["seed"],
        workouts=workouts,
    )
