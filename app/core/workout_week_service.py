"""Weekly workout plan service: persisted weekly plans with skip/duration/regen."""

from datetime import date, datetime, timedelta
import random
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.progression_service import suggest_weight_kg
from app.core.workout_service import (
    DAY_TYPE_MUSCLES,
    DAY_TYPE_TITLES,
    LOWER_MUSCLES,
    MAIN_EXERCISE_TYPES,
    ACCESSORY_EXERCISE_TYPES,
    MAIN_PRESCRIPTION,
    ACCESSORY_PRESCRIPTION,
    SPLIT_DAY_TYPES,
    DEFAULT_SPLIT_PREFERENCE,
    DEFAULT_DAYS_PER_WEEK,
    get_onboarding_settings,
    get_eligible_exercises,
    _filter_by_day_type,
    _pick_exercises,
    _exercise_to_schema,
)
from app.models.workout_week_plan import WorkoutWeekPlan
from app.schemas.workout import (
    WorkoutBlockItemOut,
    WorkoutExerciseBlockOut,
    WorkoutPrescriptionOut,
)
from app.schemas.workout_week import WorkoutDayOut, WorkoutWeekResponseOut


# Day templates: which days of week to schedule workouts (0=Monday, 6=Sunday)
DAYS_PER_WEEK_TEMPLATES = {
    2: [0, 3],           # Mon, Thu
    3: [0, 2, 4],        # Mon, Wed, Fri
    4: [0, 1, 3, 4],     # Mon, Tue, Thu, Fri
    5: [0, 1, 2, 3, 4],  # Mon-Fri
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


def _get_exercise_counts(duration_minutes: int) -> tuple[int, int]:
    """Get (main_count, accessory_count) based on duration."""
    for threshold, counts in sorted(DURATION_EXERCISE_COUNTS.items()):
        if duration_minutes <= threshold:
            return counts
    # Fallback for very long durations
    return (3, 4)


def get_week_start(target_date: date) -> date:
    """Get the Monday of the week containing target_date."""
    return target_date - timedelta(days=target_date.weekday())


def _get_template_dates(week_start: date, days_per_week: int) -> list[date]:
    """Get the workout dates for a week based on days_per_week template."""
    capped_days = max(2, min(5, days_per_week))
    day_offsets = DAYS_PER_WEEK_TEMPLATES.get(capped_days, DAYS_PER_WEEK_TEMPLATES[4])
    return [week_start + timedelta(days=offset) for offset in day_offsets]


def _get_next_day_type_in_rotation(split_preference: str, previous_day_type: Optional[str]) -> str:
    """Get the next day type in the rotation based on the previous one."""
    day_types = SPLIT_DAY_TYPES.get(split_preference, SPLIT_DAY_TYPES["upper_lower"])
    
    if not previous_day_type or previous_day_type not in day_types:
        return day_types[0]
    
    current_index = day_types.index(previous_day_type)
    next_index = (current_index + 1) % len(day_types)
    return day_types[next_index]


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
    return base.model_copy(update={"suggested_weight_kg": suggestion.weight_kg})


def _generate_single_workout(
    *,
    rng: random.Random,
    all_eligible: list,
    day_type: str,
    split_preference: str,
    duration_minutes: int,
    workout_date: date,
    slot_index: int,
    workout_day_id: Optional[str] = None,
    db: Optional[Session] = None,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Generate a single workout day entry for the plan.
    Returns a dict matching the WorkoutDayOut schema structure.
    """
    if workout_day_id is None:
        workout_day_id = str(uuid.uuid4())

    day_pool = _filter_by_day_type(all_eligible, day_type)

    # Fallback if pool too small
    if len(day_pool) < 4:
        day_pool = all_eligible

    main_count, accessory_count = _get_exercise_counts(duration_minutes)
    selected_ids: set[str] = set()

    # Pick main exercises
    main_exercises = _pick_exercises(
        rng,
        day_pool,
        count=main_count,
        preferred_types=MAIN_EXERCISE_TYPES,
        exclude_ids=selected_ids,
    )
    selected_ids.update(ex.id for ex in main_exercises)

    # Pick accessory exercises
    accessory_exercises = _pick_exercises(
        rng,
        day_pool,
        count=accessory_count,
        preferred_types=ACCESSORY_EXERCISE_TYPES,
        exclude_ids=selected_ids,
    )

    # Build exercise blocks
    main_block = WorkoutExerciseBlockOut(
        block_type="main",
        items=[
            WorkoutBlockItemOut(
                exercise=_exercise_to_schema(ex),
                prescription=_prescription_with_suggestion(
                    db, user_id=user_id, exercise=ex, base=MAIN_PRESCRIPTION
                ),
            )
            for ex in main_exercises
        ],
    )

    accessory_block = WorkoutExerciseBlockOut(
        block_type="accessory",
        items=[
            WorkoutBlockItemOut(
                exercise=_exercise_to_schema(ex),
                prescription=_prescription_with_suggestion(
                    db, user_id=user_id, exercise=ex, base=ACCESSORY_PRESCRIPTION
                ),
            )
            for ex in accessory_exercises
        ],
    )

    title = DAY_TYPE_TITLES.get(day_type, "Workout")

    return {
        "workoutDayId": workout_day_id,
        "slotIndex": slot_index,
        "date": workout_date.isoformat(),
        "durationMinutes": duration_minutes,
        "title": title,
        "splitKey": split_preference,
        "dayType": day_type,
        "estimatedMinutes": duration_minutes,
        "exerciseBlocks": [
            main_block.model_dump(by_alias=True),
            accessory_block.model_dump(by_alias=True),
        ],
    }


def _generate_week_plan(
    db: Session,
    *,
    user_id: str,
    week_start: date,
    owned_equipment_ids: set[str],
    split_preference: str,
    days_per_week: int,
    existing_durations: Optional[dict[int, int]] = None,
    existing_workout_day_ids: Optional[dict[int, str]] = None,
    force_new_ids: bool = False,
    anchor_date: Optional[date] = None,
) -> dict[str, Any]:
    """
    Generate a complete week plan.

    Args:
        existing_durations: Dict of slotIndex -> durationMinutes to preserve
        existing_workout_day_ids: Dict of slotIndex -> workoutDayId to preserve (ignored if force_new_ids)
        force_new_ids: If True, generate new workoutDayIds for all entries
        anchor_date: If provided, the first workout on/after this date starts the rotation
    """
    capped_days = max(2, min(5, days_per_week))
    template_dates = _get_template_dates(week_start, capped_days)

    # Determine seed
    if force_new_ids:
        seed_str = f"{user_id}:{week_start.isoformat()}:{uuid.uuid4()}"
    else:
        seed_str = f"{user_id}:{week_start.isoformat()}"
    rng = random.Random(seed_str)

    # Rotation anchor: find the first date >= anchor_date (or today) and start rotation there
    if anchor_date is None:
        anchor_date = date.today()

    # Find the anchor slot index (first scheduled date >= anchor_date)
    anchor_slot_index = 0
    for i, d in enumerate(template_dates):
        if d >= anchor_date:
            anchor_slot_index = i
            break
    else:
        # All dates are in the past; anchor to first slot
        anchor_slot_index = 0

    # Fetch eligible exercises once for the entire week
    all_eligible = get_eligible_exercises(db, owned_equipment_ids=owned_equipment_ids)

    workouts: list[dict[str, Any]] = []
    previous_day_type: Optional[str] = None

    for slot_index, workout_date in enumerate(template_dates):
        # Determine day type via rotation
        if slot_index == anchor_slot_index:
            # Start of rotation
            day_type = SPLIT_DAY_TYPES.get(split_preference, SPLIT_DAY_TYPES["upper_lower"])[0]
        else:
            day_type = _get_next_day_type_in_rotation(split_preference, previous_day_type)

        previous_day_type = day_type

        # Get duration (preserved or default)
        duration = DEFAULT_DURATION_MINUTES
        if existing_durations and slot_index in existing_durations:
            duration = existing_durations[slot_index]

        # Get workout_day_id (preserved or new)
        workout_day_id = None
        if not force_new_ids and existing_workout_day_ids and slot_index in existing_workout_day_ids:
            workout_day_id = existing_workout_day_ids[slot_index]

        workout = _generate_single_workout(
            rng=rng,
            all_eligible=all_eligible,
            day_type=day_type,
            split_preference=split_preference,
            duration_minutes=duration,
            workout_date=workout_date,
            slot_index=slot_index,
            workout_day_id=workout_day_id,
            db=db,
            user_id=user_id,
        )
        workouts.append(workout)

    return {
        "weekStart": week_start.isoformat(),
        "daysPerWeek": capped_days,
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "seed": seed_str,
        "workouts": workouts,
    }


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
    split_preference = settings["split_preference"]
    days_per_week = settings.get("days_per_week", DEFAULT_DAYS_PER_WEEK)
    owned_equipment_ids = _get_owned_equipment_ids_from_settings(db, settings)

    existing_plan = get_week_plan(db, user_id=user_id, week_start=week_start)

    if existing_plan and not force_regenerate:
        # Return existing plan
        return _plan_json_to_response(existing_plan.plan_json)

    # Extract existing durations to preserve (by slotIndex)
    existing_durations: Optional[dict[int, int]] = None
    if existing_plan and force_regenerate:
        existing_durations = {}
        for w in existing_plan.plan_json.get("workouts", []):
            slot_idx = w.get("slotIndex", 0)
            existing_durations[slot_idx] = w.get("durationMinutes", DEFAULT_DURATION_MINUTES)

    # Generate new plan
    plan_json = _generate_week_plan(
        db,
        user_id=user_id,
        week_start=week_start,
        owned_equipment_ids=owned_equipment_ids,
        split_preference=split_preference,
        days_per_week=days_per_week,
        existing_durations=existing_durations,
        existing_workout_day_ids=None,  # force_regenerate creates new IDs
        force_new_ids=force_regenerate,
        anchor_date=date.today(),
    )

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

    workouts.pop(removed_idx)

    # Load settings
    settings = get_onboarding_settings(db, user_id=user_id)
    split_preference = settings["split_preference"]
    days_per_week = settings.get("days_per_week", DEFAULT_DAYS_PER_WEEK)
    owned_equipment_ids = _get_owned_equipment_ids_from_settings(db, settings)

    # Re-assign slotIndex and dates
    capped_days = max(2, min(5, days_per_week))
    template_dates = _get_template_dates(week_start, capped_days)

    for i, w in enumerate(workouts):
        w["slotIndex"] = i
        if i < len(template_dates):
            w["date"] = template_dates[i].isoformat()

    # Determine the next day type from the last workout in list
    last_day_type = None
    if workouts:
        last_day_type = workouts[-1].get("dayType")

    new_day_type = _get_next_day_type_in_rotation(split_preference, last_day_type)

    # Generate the new workout
    new_slot_index = len(workouts)
    new_date = template_dates[new_slot_index] if new_slot_index < len(template_dates) else template_dates[-1]

    # Use a random seed for the new workout
    rng = random.Random(f"{user_id}:{week_start.isoformat()}:{uuid.uuid4()}")
    all_eligible = get_eligible_exercises(db, owned_equipment_ids=owned_equipment_ids)

    new_workout = _generate_single_workout(
        rng=rng,
        all_eligible=all_eligible,
        day_type=new_day_type,
        split_preference=split_preference,
        duration_minutes=DEFAULT_DURATION_MINUTES,
        workout_date=new_date,
        slot_index=new_slot_index,
        workout_day_id=None,  # New ID
        db=db,
        user_id=user_id,
    )

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

    # Extract durations and IDs by slotIndex
    existing_durations: dict[int, int] = {}
    existing_workout_day_ids: dict[int, str] = {}
    for w in workouts:
        slot_idx = w.get("slotIndex", 0)
        existing_durations[slot_idx] = w.get("durationMinutes", DEFAULT_DURATION_MINUTES)
        existing_workout_day_ids[slot_idx] = w.get("workoutDayId")

    # Load settings
    settings = get_onboarding_settings(db, user_id=user_id)
    split_preference = settings["split_preference"]
    days_per_week = settings.get("days_per_week", DEFAULT_DAYS_PER_WEEK)
    owned_equipment_ids = _get_owned_equipment_ids_from_settings(db, settings)

    # Regenerate week with preserved IDs and durations
    new_plan_json = _generate_week_plan(
        db,
        user_id=user_id,
        week_start=week_start,
        owned_equipment_ids=owned_equipment_ids,
        split_preference=split_preference,
        days_per_week=days_per_week,
        existing_durations=existing_durations,
        existing_workout_day_ids=existing_workout_day_ids,
        force_new_ids=False,
        anchor_date=date.today(),
    )

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


def _get_owned_equipment_ids_from_settings(db: Session, settings: dict) -> set[str]:
    """Extract and validate equipment IDs from onboarding settings."""
    from app.models.equipment import Equipment

    equipment_slugs = settings.get("equipment_ids", [])
    if not isinstance(equipment_slugs, list) or not equipment_slugs:
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

