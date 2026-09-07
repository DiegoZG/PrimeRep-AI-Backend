"""Workout generation service: rules-based split workouts with history-aware rotation."""

from dataclasses import dataclass
from datetime import date, timedelta
import random
import uuid
from typing import Any, Optional, Union

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
FULL_BODY_MUSCLES = UPPER_MUSCLES | LOWER_MUSCLES | {"abs"}

CUSTOM_MUSCLE_ALIASES = {
    "lower-back": "back",
    "adductors": "quads",
    "abductors": "glutes",
    "trapezius": "back",
}

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
    "chest": {"chest"},
    "back": {"back"},
    "shoulders": {"shoulders"},
    "arms": {"biceps", "triceps", "forearms"},
}

DAY_TYPE_TITLES = {
    "upper": "Upper A",
    "lower": "Lower A",
    "push": "Push Day",
    "pull": "Pull Day",
    "legs": "Leg Day",
    "full_body": "Full Body",
    "chest": "Chest",
    "back": "Back",
    "shoulders": "Shoulders",
    "arms": "Arms",
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

FITNESS_GOAL_ALIASES = {
    "build-muscle": "hypertrophy",
    "build_muscle": "hypertrophy",
    "hypertrophy": "hypertrophy",
    "general-fitness": "general_fitness",
    "general_fitness": "general_fitness",
    "conditioning": "conditioning",
    "get-stronger": "strength",
    "get_stronger": "strength",
    "strength": "strength",
}

EXPERIENCE_LEVEL_ALIASES = {
    "no-experience": "no-experience",
    "no_experience": "no-experience",
    "novice": "no-experience",
    "beginner": "beginner",
    "intermediate": "intermediate",
    "advanced": "advanced",
}

VARIETY_LEVEL_ALIASES = {
    "consistent": "consistent",
    "balanced": "balanced",
    "varied": "varied",
}


@dataclass(frozen=True)
class WorkoutDayDefinition:
    day_type: str
    title: str
    muscles: frozenset[str]
    restrict_to_muscles: bool = False


@dataclass(frozen=True)
class OnboardingWorkoutSettings:
    equipment_ids: tuple[str, ...]
    split_preference: str
    goal: str
    days_per_week: int
    focus_muscles: tuple[str, ...]
    experience_level: Optional[str]
    variety_level: Optional[str]
    day_cycle: tuple[WorkoutDayDefinition, ...]

    @property
    def selection_seed(self) -> str:
        cycle = ";".join(
            f"{day.day_type}:{','.join(sorted(day.muscles))}" for day in self.day_cycle
        )
        return ":".join(
            (
                self.split_preference,
                self.goal,
                self.experience_level or "",
                self.variety_level or "",
                ",".join(self.equipment_ids),
                cycle,
            )
        )


_MISSING = object()

FREQUENCY_DAYS = {
    "1-day": 1,
    "2-days": 2,
    "3-days": 3,
    "4-days": 4,
    "5-days": 5,
    "6-days": 6,
    "every-day": 7,
}

SPLIT_CYCLES = {
    "push-pull-legs": ("push", "pull", "legs"),
    "upper-lower": ("upper", "lower"),
    "push-pull-legs-full-body": ("push", "pull", "legs", "full_body"),
    "push-pull-legs-upper-lower": ("push", "pull", "legs", "upper", "lower"),
    "upper-lower-full-body": ("upper", "lower", "full_body"),
    "full-body": ("full_body",),
    "bro-split": ("chest", "back", "legs", "shoulders", "arms"),
    "lower-focused-upper": ("lower", "upper", "lower"),
    "push-pull-legs-upper-body": ("push", "pull", "legs", "upper"),
    "upper_lower": ("upper", "lower"),
    "ppl": ("push", "pull", "legs"),
    "full_body": ("full_body",),
}


def _canonical_or_legacy(
    data: dict[str, Any],
    canonical: str,
    legacy: Union[str, tuple[str, ...]],
    default: Any,
) -> Any:
    value = data.get(canonical, _MISSING)
    if value is not _MISSING and value not in (None, "", [], {}):
        return value
    legacy_keys = (legacy,) if isinstance(legacy, str) else legacy
    for key in legacy_keys:
        value = data.get(key, _MISSING)
        if value is not _MISSING and value not in (None, "", [], {}):
            return value
    return default


def _normalize_string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    normalized = (
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    )
    return tuple(dict.fromkeys(normalized))


def _normalize_alias(value: Any, aliases: dict[str, str], default: Optional[str]) -> Optional[str]:
    if not isinstance(value, str):
        return default
    return aliases.get(value.strip().lower(), default)


def _normalize_days_per_week(data: dict[str, Any]) -> int:
    raw = data.get("workoutFrequency", _MISSING)
    if raw is not _MISSING and raw not in (None, ""):
        if not isinstance(raw, str):
            return DEFAULT_DAYS_PER_WEEK
        return FREQUENCY_DAYS.get(raw.strip().lower(), DEFAULT_DAYS_PER_WEEK)

    legacy = _canonical_or_legacy(
        data,
        "__workout_frequency_unset__",
        ("workout_frequency", "days_per_week"),
        DEFAULT_DAYS_PER_WEEK,
    )
    if isinstance(legacy, str) and legacy.strip().lower() in FREQUENCY_DAYS:
        return FREQUENCY_DAYS[legacy.strip().lower()]
    if isinstance(legacy, bool):
        return DEFAULT_DAYS_PER_WEEK
    try:
        days = int(legacy)
    except (TypeError, ValueError):
        return DEFAULT_DAYS_PER_WEEK
    return max(1, min(7, days))


def _standard_day(day_type: str) -> WorkoutDayDefinition:
    return WorkoutDayDefinition(
        day_type=day_type,
        title=DAY_TYPE_TITLES.get(day_type, "Workout"),
        muscles=frozenset(DAY_TYPE_MUSCLES.get(day_type, FULL_BODY_MUSCLES)),
    )


def _optimized_cycle(days_per_week: int) -> tuple[str, ...]:
    if days_per_week <= 3:
        return ("full_body",)
    if days_per_week == 4:
        return ("upper", "lower")
    if days_per_week == 5:
        return ("push", "pull", "legs", "upper", "lower")
    return ("push", "pull", "legs")


def _normalize_custom_cycle(value: Any) -> tuple[WorkoutDayDefinition, ...]:
    if not isinstance(value, list):
        return ()

    cycle: list[WorkoutDayDefinition] = []
    for index, workout in enumerate(value):
        if not isinstance(workout, dict):
            continue
        submitted_muscles = _normalize_string_list(
            _canonical_or_legacy(workout, "muscleGroups", "muscle_groups", [])
        )
        muscles = tuple(
            dict.fromkeys(
                normalized
                for muscle in submitted_muscles
                if (normalized := CUSTOM_MUSCLE_ALIASES.get(muscle, muscle))
                in FULL_BODY_MUSCLES
            )
        )
        if not muscles:
            continue
        workout_id = workout.get("id")
        identifier = workout_id if isinstance(workout_id, str) and workout_id else str(index)
        name = workout.get("name")
        title = name if isinstance(name, str) and name else f"Custom Workout {index + 1}"
        cycle.append(
            WorkoutDayDefinition(
                day_type=f"custom:{identifier}",
                title=title,
                muscles=frozenset(muscles),
                restrict_to_muscles=True,
            )
        )
    return tuple(cycle)


def normalize_onboarding_settings(data: dict[str, Any]) -> OnboardingWorkoutSettings:
    days_per_week = _normalize_days_per_week(data)
    raw_split = _canonical_or_legacy(
        data,
        "workoutSplit",
        ("workout_split", "split_preference"),
        DEFAULT_SPLIT_PREFERENCE,
    )
    normalized_split = raw_split.strip().lower() if isinstance(raw_split, str) else None
    split_preference = (
        normalized_split
        if normalized_split in {*SPLIT_CYCLES, "ai-optimized", "custom"}
        else DEFAULT_SPLIT_PREFERENCE
    )

    custom_value = _canonical_or_legacy(data, "customWorkouts", "custom_workouts", [])
    custom_cycle = _normalize_custom_cycle(custom_value)
    if split_preference == "custom" and custom_cycle:
        day_cycle = custom_cycle
    else:
        cycle_keys = (
            _optimized_cycle(days_per_week)
            if split_preference in {"ai-optimized", "custom"}
            else SPLIT_CYCLES[split_preference]
        )
        day_cycle = tuple(_standard_day(day_type) for day_type in cycle_keys)

    equipment = tuple(
        sorted(
            _normalize_string_list(
                _canonical_or_legacy(
                    data,
                    "selectedEquipment",
                    ("selected_equipment", "equipment_ids"),
                    [],
                )
            )
        )
    )
    goal = _normalize_alias(
        _canonical_or_legacy(data, "fitnessGoal", ("fitness_goal", "goal"), DEFAULT_GOAL),
        FITNESS_GOAL_ALIASES,
        DEFAULT_GOAL,
    )
    experience = _normalize_alias(
        _canonical_or_legacy(data, "experienceLevel", "experience_level", None),
        EXPERIENCE_LEVEL_ALIASES,
        None,
    )
    variety = _normalize_alias(
        _canonical_or_legacy(data, "varietyLevel", "variety_level", None),
        VARIETY_LEVEL_ALIASES,
        None,
    )
    focus_muscles = _normalize_string_list(
        _canonical_or_legacy(data, "focusMuscles", "focus_muscles", [])
    )

    return OnboardingWorkoutSettings(
        equipment_ids=equipment,
        split_preference=split_preference,
        goal=goal or DEFAULT_GOAL,
        days_per_week=days_per_week,
        focus_muscles=focus_muscles,
        experience_level=experience,
        variety_level=variety,
        day_cycle=day_cycle,
    )


def get_onboarding_settings(db: Session, *, user_id: str) -> OnboardingWorkoutSettings:
    """
    Load onboarding settings with defaults.
    Camel-case mobile fields are canonical; legacy snake-case fields are fallbacks.
    """
    profile = (
        db.query(OnboardingProfile)
        .filter(OnboardingProfile.user_id == user_id)
        .first()
    )

    data = profile.data if profile and profile.data else {}

    return normalize_onboarding_settings(data)


def get_user_equipment_ids(db: Session, *, user_id: str) -> set[str]:
    """
    Load user equipment slugs from onboarding JSON and validate them against the
    equipment table. Returns a set of valid equipment ids.
    """
    settings = get_onboarding_settings(db, user_id=user_id)
    equipment_slugs = settings.equipment_ids

    if not equipment_slugs:
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
        .order_by(Exercise.id.asc())
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
    muscles: Optional[frozenset[str]] = None,
) -> list[Exercise]:
    """Filter exercises by primary muscle matching the day type."""
    target_muscles = muscles or frozenset(DAY_TYPE_MUSCLES.get(day_type, UPPER_MUSCLES))
    return [ex for ex in exercises if ex.primary_muscle in target_muscles]


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
    split_preference = settings.split_preference

    # Get last workout for rotation logic
    last_workout = get_last_workout(db, user_id=user_id)

    # Determine today's day type
    day_types = [day.day_type for day in settings.day_cycle]
    if not last_workout or last_workout.workout_date < target_date - timedelta(days=1):
        day = settings.day_cycle[0]
    elif last_workout.day_type in day_types:
        day = settings.day_cycle[(day_types.index(last_workout.day_type) + 1) % len(day_types)]
    else:
        day = settings.day_cycle[0]

    day_type = day.day_type
    title = day.title
    split_key = split_preference

    # Get eligible exercises
    all_eligible = get_eligible_exercises(db, owned_equipment_ids=owned_equipment_ids)
    day_pool = _filter_by_day_type(all_eligible, day_type, day.muscles)

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
