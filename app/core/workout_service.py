"""Workout generation service: rules-based split workouts with history-aware rotation."""

from datetime import date, timedelta
import random
import uuid
from typing import Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from app.models.equipment import Equipment
from app.models.exercise import Exercise
from app.models.onboarding_profile import OnboardingProfile
from app.models.workout_day_history import WorkoutDayHistory
from app.schemas.workout import (
    WorkoutBlockItemOut,
    WorkoutExerciseBlockOut,
    WorkoutExerciseOut,
    WorkoutNextResponseOut,
    WorkoutPrescriptionOut,
)


# Muscle groups for day filtering
UPPER_MUSCLES = {"chest", "shoulders", "back", "biceps", "triceps", "forearms", "neck"}
LOWER_MUSCLES = {"quads", "hamstrings", "glutes", "calves"}
PUSH_MUSCLES = {"chest", "shoulders", "triceps"}
PULL_MUSCLES = {"back", "biceps", "forearms"}
LEGS_MUSCLES = {"quads", "hamstrings", "glutes", "calves"}
FULL_BODY_MUSCLES = UPPER_MUSCLES | LOWER_MUSCLES

# Day type mappings for different splits
SPLIT_DAY_TYPES = {
    "upper_lower": ["upper", "lower"],
    "ppl": ["push", "pull", "legs"],
    "full_body": ["full_body"],
}

DAY_TYPE_MUSCLES = {
    "upper": UPPER_MUSCLES,
    "lower": LOWER_MUSCLES,
    "push": PUSH_MUSCLES,
    "pull": PULL_MUSCLES,
    "legs": LEGS_MUSCLES,
    "full_body": FULL_BODY_MUSCLES,
}

DAY_TYPE_TITLES = {
    "upper": "Upper A",
    "lower": "Lower A",
    "push": "Push Day",
    "pull": "Pull Day",
    "legs": "Leg Day",
    "full_body": "Full Body",
}

# Prescriptions
MAIN_PRESCRIPTION = WorkoutPrescriptionOut(
    sets=4,
    reps_min=6,
    reps_max=10,
    rest_seconds=120,
)

ACCESSORY_PRESCRIPTION = WorkoutPrescriptionOut(
    sets=3,
    reps_min=10,
    reps_max=15,
    rest_seconds=75,
)

MAIN_EXERCISE_TYPES = {"strength", "bodyweight", "olympic"}
ACCESSORY_EXERCISE_TYPES = {"accessory"}

ESTIMATED_MINUTES = 45

# Default onboarding values
DEFAULT_SPLIT_PREFERENCE = "upper_lower"
DEFAULT_GOAL = "hypertrophy"
DEFAULT_DAYS_PER_WEEK = 4


def get_onboarding_settings(db: Session, *, user_id: str) -> dict:
    """
    Load onboarding settings with defaults.
    Returns dict with: equipment_ids, split_preference, goal, days_per_week, focus_muscles.
    """
    profile = (
        db.query(OnboardingProfile)
        .filter(OnboardingProfile.user_id == user_id)
        .first()
    )

    data = profile.data if profile and profile.data else {}

    return {
        "equipment_ids": data.get("equipment_ids", []),
        "split_preference": data.get("split_preference", DEFAULT_SPLIT_PREFERENCE),
        "goal": data.get("goal", DEFAULT_GOAL),
        "days_per_week": data.get("days_per_week", DEFAULT_DAYS_PER_WEEK),
        "focus_muscles": data.get("focus_muscles", []),
    }


def get_user_equipment_ids(db: Session, *, user_id: str) -> set[str]:
    """
    Load user equipment slugs from onboarding JSON and validate them against the
    equipment table. Returns a set of valid equipment ids.
    """
    settings = get_onboarding_settings(db, user_id=user_id)
    equipment_slugs = settings["equipment_ids"]

    if not isinstance(equipment_slugs, list) or not equipment_slugs:
        return set()

    # Validate slugs exist in equipment table (Equipment.id is the slug)
    valid_ids = (
        db.query(Equipment.id)
        .filter(Equipment.id.in_(equipment_slugs))
        .all()
    )

    return {row[0] for row in valid_ids}


def get_last_workout(db: Session, *, user_id: str) -> Optional[WorkoutDayHistory]:
    """Get the most recent workout history record for the user."""
    return (
        db.query(WorkoutDayHistory)
        .filter(WorkoutDayHistory.user_id == user_id)
        .order_by(WorkoutDayHistory.workout_date.desc())
        .first()
    )


def save_workout_history(
    db: Session,
    *,
    user_id: str,
    workout_date: date,
    day_type: str,
) -> None:
    """
    Upsert today's workout history record.
    If a record exists for this user+date, update the day_type.
    """
    stmt = (
        pg_insert(WorkoutDayHistory)
        .values(
            id=str(uuid.uuid4()),
            user_id=user_id,
            workout_date=workout_date,
            day_type=day_type,
        )
        .on_conflict_do_update(
            constraint="uq_workout_day_history_user_date",
            set_={"day_type": day_type},
        )
    )
    db.execute(stmt)
    db.commit()


def get_next_day_type(
    *,
    split_preference: str,
    last_workout: Optional[WorkoutDayHistory],
    target_date: date,
) -> str:
    """
    Determine the next day type based on split preference and history.

    Rules:
    - If no history: start with first day of split
    - If last workout was yesterday (or today): rotate to next day type
    - Otherwise: start fresh with first day of split
    """
    day_types = SPLIT_DAY_TYPES.get(split_preference, SPLIT_DAY_TYPES["upper_lower"])

    if not last_workout:
        # First workout ever: start with first day type
        return day_types[0]

    yesterday = target_date - timedelta(days=1)

    # Check if last workout was yesterday or today (consecutive training)
    if last_workout.workout_date >= yesterday:
        # Rotate to next day type
        last_day_type = last_workout.day_type
        if last_day_type in day_types:
            current_index = day_types.index(last_day_type)
            next_index = (current_index + 1) % len(day_types)
            return day_types[next_index]
        else:
            # Last day type doesn't match current split (user changed splits)
            return day_types[0]
    else:
        # Gap in training: start fresh
        return day_types[0]


def get_eligible_exercises(
    db: Session,
    *,
    owned_equipment_ids: set[str],
) -> list[Exercise]:
    """
    Return active exercises that the user can perform based on their equipment.
    Eligible if: requires no equipment OR all required equipment is owned.
    """
    exercises = (
        db.query(Exercise)
        .options(selectinload(Exercise.equipment))
        .filter(Exercise.is_active.is_(True))
        .all()
    )

    eligible: list[Exercise] = []
    for exercise in exercises:
        required = {e.id for e in exercise.equipment}
        if not required or required <= owned_equipment_ids:
            eligible.append(exercise)

    return eligible


def _filter_by_day_type(
    exercises: list[Exercise],
    day_type: str,
) -> list[Exercise]:
    """Filter exercises by primary muscle matching the day type."""
    muscles = DAY_TYPE_MUSCLES.get(day_type, UPPER_MUSCLES)
    return [ex for ex in exercises if ex.primary_muscle in muscles]


def _pick_exercises(
    rng: random.Random,
    pool: list[Exercise],
    count: int,
    preferred_types: set[str],
    exclude_ids: set[str],
) -> list[Exercise]:
    """
    Pick up to `count` exercises from pool, preferring certain types.
    Avoids duplicates by excluding already-selected ids.
    """
    available = [ex for ex in pool if ex.id not in exclude_ids]

    # Prefer exercises matching the preferred types
    preferred = [ex for ex in available if ex.exercise_type in preferred_types]
    fallback = [ex for ex in available if ex.exercise_type not in preferred_types]

    rng.shuffle(preferred)
    rng.shuffle(fallback)

    selected: list[Exercise] = []
    for ex in preferred:
        if len(selected) >= count:
            break
        selected.append(ex)

    for ex in fallback:
        if len(selected) >= count:
            break
        selected.append(ex)

    return selected


def _exercise_to_schema(exercise: Exercise) -> WorkoutExerciseOut:
    """Convert ORM exercise to schema."""
    return WorkoutExerciseOut(
        id=exercise.id,
        name=exercise.name,
        exercise_type=exercise.exercise_type,
        primary_muscle=exercise.primary_muscle,
        secondary_muscles=exercise.secondary_muscles or [],
        image_url=exercise.image_url,
        demo_video_url=exercise.demo_video_url,
        required_equipment_ids=[e.id for e in exercise.equipment],
    )


def generate_next_workout(
    db: Session,
    *,
    user_id: str,
    owned_equipment_ids: set[str],
    target_date: Optional[date] = None,
    force_regenerate: bool = False,
) -> WorkoutNextResponseOut:
    """
    Generate the next workout for the user based on their equipment,
    onboarding preferences, and workout history.

    Args:
        force_regenerate: If True, generates a new random workout instead of
                         returning the deterministic cached workout for today.
    """
    if target_date is None:
        target_date = date.today()

    # Load onboarding settings
    settings = get_onboarding_settings(db, user_id=user_id)
    split_preference = settings["split_preference"]

    # Get last workout for rotation logic
    last_workout = get_last_workout(db, user_id=user_id)

    # Determine today's day type
    day_type = get_next_day_type(
        split_preference=split_preference,
        last_workout=last_workout,
        target_date=target_date,
    )

    title = DAY_TYPE_TITLES.get(day_type, "Workout")
    split_key = split_preference

    # Get eligible exercises
    all_eligible = get_eligible_exercises(db, owned_equipment_ids=owned_equipment_ids)
    day_pool = _filter_by_day_type(all_eligible, day_type)

    # If day pool is too small, use all eligible (fallback)
    if len(day_pool) < 4:
        day_pool = all_eligible

    # Deterministic random for stable output per user per day
    # When force_regenerate is True, add a random UUID to get a new workout
    if force_regenerate:
        seed_str = f"{user_id}:{target_date.isoformat()}:{uuid.uuid4()}"
    else:
        seed_str = f"{user_id}:{target_date.isoformat()}"
    rng = random.Random(seed_str)

    selected_ids: set[str] = set()

    # Pick 2 main exercises
    main_exercises = _pick_exercises(
        rng,
        day_pool,
        count=2,
        preferred_types=MAIN_EXERCISE_TYPES,
        exclude_ids=selected_ids,
    )
    selected_ids.update(ex.id for ex in main_exercises)

    # Pick 2 accessory exercises
    accessory_exercises = _pick_exercises(
        rng,
        day_pool,
        count=2,
        preferred_types=ACCESSORY_EXERCISE_TYPES,
        exclude_ids=selected_ids,
    )
    selected_ids.update(ex.id for ex in accessory_exercises)

    # Build blocks
    main_block = WorkoutExerciseBlockOut(
        block_type="main",
        items=[
            WorkoutBlockItemOut(
                exercise=_exercise_to_schema(ex),
                prescription=MAIN_PRESCRIPTION,
            )
            for ex in main_exercises
        ],
    )

    accessory_block = WorkoutExerciseBlockOut(
        block_type="accessory",
        items=[
            WorkoutBlockItemOut(
                exercise=_exercise_to_schema(ex),
                prescription=ACCESSORY_PRESCRIPTION,
            )
            for ex in accessory_exercises
        ],
    )

    # Save today's workout to history
    save_workout_history(
        db,
        user_id=user_id,
        workout_date=target_date,
        day_type=day_type,
    )

    return WorkoutNextResponseOut(
        workout_id=str(uuid.uuid4()),
        title=title,
        split_key=split_key,
        day_type=day_type,
        estimated_minutes=ESTIMATED_MINUTES,
        exercise_blocks=[main_block, accessory_block],
    )
