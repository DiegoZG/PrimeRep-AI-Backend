from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import uuid
from typing import Optional

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.core.exercise_service import exercise_to_dict, exercise_visibility_filter, get_exercise
from app.core.schedule_lock import lock_user_schedule
from app.core.training_preferences_service import get_training_preferences
from app.models.equipment import Equipment
from app.models.exercise import Exercise
from app.models.workout_session import WorkoutSession
from app.models.workout_template import (
    ExerciseAlias,
    UserProgramActivation,
    WorkoutTemplate,
    WorkoutTemplateAlias,
    WorkoutTemplateDay,
    WorkoutTemplateEquipment,
    WorkoutTemplateExercise,
)
from app.models.workout_week_plan import WorkoutWeekPlan
from app.schemas.exercise import ExerciseOut
from app.schemas.workout_template import (
    ActiveProgramOut,
    EquipmentConflictOut,
    ScheduleImpactOut,
    TemplateDayOut,
    TemplateExerciseOut,
    WorkoutTemplateDetailOut,
    WorkoutTemplateSummaryOut,
    WorkoutTemplateWrite,
)


class TemplateNotFoundError(Exception):
    pass


class TemplateConflictError(Exception):
    pass


class TemplateValidationError(Exception):
    pass


def _visible_template_query(db: Session, user_id: str):
    return db.query(WorkoutTemplate).filter(
        WorkoutTemplate.is_archived.is_(False),
        or_(
            WorkoutTemplate.scope == "system",
            WorkoutTemplate.owner_user_id == user_id,
        ),
    )


def _template_query(db: Session, user_id: str):
    return _visible_template_query(db, user_id).options(
            selectinload(WorkoutTemplate.aliases),
            selectinload(WorkoutTemplate.equipment_requirements).selectinload(
                WorkoutTemplateEquipment.equipment
            ),
            selectinload(WorkoutTemplate.days)
            .selectinload(WorkoutTemplateDay.exercises)
            .selectinload(WorkoutTemplateExercise.exercise)
            .selectinload(Exercise.equipment),
        )


def get_template(db: Session, user_id: str, template_id: str) -> WorkoutTemplate:
    template = _template_query(db, user_id).filter(WorkoutTemplate.id == template_id).first()
    if template is None:
        raise TemplateNotFoundError("Workout template not found")
    return template


def _owned_equipment_ids(db: Session, user_id: str) -> set[str]:
    from app.core.workout_service import get_onboarding_settings
    from app.core.workout_week_service import _get_owned_equipment_ids_from_settings

    return _get_owned_equipment_ids_from_settings(
        db, get_onboarding_settings(db, user_id=user_id)
    )


def _frequency_preference(preferences: dict) -> Optional[int]:
    value = preferences.get("workoutFrequency")
    if value == "every-day":
        return 7
    if isinstance(value, str) and value.endswith("-days"):
        try:
            return int(value.split("-", 1)[0])
        except ValueError:
            return None
    if value == "1-day":
        return 1
    return None


def _recommendation(template: WorkoutTemplate, preferences: dict, owned: set[str]) -> tuple[int, list[str]]:
    score = 0.0
    possible = 0.0
    reasons: list[str] = []
    goal = preferences.get("fitnessGoal")
    if goal:
        possible += 30
        if template.goal == goal:
            score += 30
            reasons.append("Matches your training goal")
    level = preferences.get("experienceLevel")
    if level:
        possible += 20
        order = ["no-experience", "beginner", "intermediate", "advanced"]
        if template.level == level:
            score += 20
            reasons.append("Fits your experience level")
        elif template.level in order and level in order and abs(order.index(template.level) - order.index(level)) == 1:
            score += 10
    frequency = _frequency_preference(preferences)
    if frequency:
        possible += 20
        difference = abs(template.frequency - frequency)
        if difference == 0:
            score += 20
            reasons.append(f"Matches your {frequency}-day schedule")
        elif difference == 1:
            score += 10
    required = {row.equipment_id for row in template.equipment_requirements}
    possible += 20
    if not required:
        score += 20
        reasons.append("No equipment required")
    else:
        coverage = len(required & owned) / len(required)
        score += 20 * coverage
        if coverage == 1:
            reasons.append("Works with your equipment")
    # The current preference contract has no target session duration. The 10-point
    # duration dimension is intentionally omitted and the remaining score normalized.
    return (round(score / possible * 100) if possible else 0, reasons[:3])


def _summary(
    template: WorkoutTemplate,
    *,
    owned_equipment_ids: set[str],
    recommendation: Optional[tuple[int, list[str]]] = None,
):
    score, reasons = recommendation or (None, [])
    required = {row.equipment_id for row in template.equipment_requirements}
    missing = sorted(required - owned_equipment_ids)
    return WorkoutTemplateSummaryOut(
        id=template.id,
        scope=template.scope,
        name=template.name,
        description=template.description or "",
        goal=template.goal,
        level=template.level,
        frequency=template.frequency,
        durationMinutes=template.duration_minutes,
        featuredRank=template.featured_rank,
        version=template.version,
        equipmentIds=sorted(required),
        missingEquipmentIds=missing,
        equipmentCompatible=not missing,
        recommendationScore=score,
        matchReasons=reasons,
    )


def template_detail(template: WorkoutTemplate, *, db: Session, user_id: str):
    days: list[TemplateDayOut] = []
    for day in sorted(template.days, key=lambda item: item.position):
        exercises = []
        for item in sorted(day.exercises, key=lambda child: child.position):
            exercises.append(
                TemplateExerciseOut(
                    id=item.id,
                    position=item.position,
                    exerciseId=item.exercise_id,
                    blockType=item.block_type,
                    sets=item.sets,
                    repsMin=item.reps_min,
                    repsMax=item.reps_max,
                    restSeconds=item.rest_seconds,
                    cue=item.cue,
                    exercise=ExerciseOut.model_validate(
                        exercise_to_dict(item.exercise, user_id=user_id)
                    ),
                )
            )
        days.append(
            TemplateDayOut(
                id=day.id,
                position=day.position,
                title=day.title,
                dayType=day.day_type,
                exercises=exercises,
            )
        )
    base = _summary(
        template, owned_equipment_ids=_owned_equipment_ids(db, user_id)
    ).model_dump()
    return WorkoutTemplateDetailOut(
        **base,
        aliases=sorted(item.alias for item in template.aliases),
        days=days,
    )


def list_templates(
    db: Session,
    user_id: str,
    *,
    scope: Optional[str] = None,
    query: Optional[str] = None,
    goal: Optional[str] = None,
    level: Optional[str] = None,
    frequency: Optional[int] = None,
    max_duration: Optional[int] = None,
    equipment_ids: Optional[list[str]] = None,
    featured: Optional[bool] = None,
    recommended: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[WorkoutTemplateSummaryOut], int]:
    orm_query = _visible_template_query(db, user_id).options(
        selectinload(WorkoutTemplate.equipment_requirements)
    )
    if scope:
        orm_query = orm_query.filter(WorkoutTemplate.scope == scope)
    elif recommended:
        orm_query = orm_query.filter(WorkoutTemplate.scope == "system")
    if goal:
        orm_query = orm_query.filter(WorkoutTemplate.goal == goal)
    if level:
        orm_query = orm_query.filter(WorkoutTemplate.level == level)
    if frequency:
        orm_query = orm_query.filter(WorkoutTemplate.frequency == frequency)
    if max_duration:
        orm_query = orm_query.filter(WorkoutTemplate.duration_minutes <= max_duration)
    if featured is not None:
        orm_query = orm_query.filter(
            WorkoutTemplate.featured_rank.isnot(None)
            if featured
            else WorkoutTemplate.featured_rank.is_(None)
        )
    if query:
        normalized = query.strip().lower()
        alias_exact = (
            db.query(WorkoutTemplateAlias.id)
            .filter(
                WorkoutTemplateAlias.template_id == WorkoutTemplate.id,
                func.lower(WorkoutTemplateAlias.alias) == normalized,
            )
            .exists()
        )
        alias_prefix = (
            db.query(WorkoutTemplateAlias.id)
            .filter(
                WorkoutTemplateAlias.template_id == WorkoutTemplate.id,
                func.lower(WorkoutTemplateAlias.alias).startswith(normalized),
            )
            .exists()
        )
        alias_substring = (
            db.query(WorkoutTemplateAlias.id)
            .filter(
                WorkoutTemplateAlias.template_id == WorkoutTemplate.id,
                func.lower(WorkoutTemplateAlias.alias).contains(normalized),
            )
            .exists()
        )
        alias_similarity = (
            db.query(func.max(func.similarity(func.lower(WorkoutTemplateAlias.alias), normalized)))
            .filter(WorkoutTemplateAlias.template_id == WorkoutTemplate.id)
            .correlate(WorkoutTemplate)
            .scalar_subquery()
        )
        name = func.lower(WorkoutTemplate.name)
        similarity = func.greatest(
            func.similarity(name, normalized),
            func.coalesce(alias_similarity, 0.0),
        )
        exact_rank = case((or_(name == normalized, alias_exact), 0), else_=1)
        prefix_rank = case((or_(name.startswith(normalized), alias_prefix), 0), else_=1)
        substring_rank = case((or_(name.contains(normalized), alias_substring), 0), else_=1)
        orm_query = orm_query.filter(
            or_(
                name.contains(normalized),
                alias_substring,
                similarity >= 0.3,
            )
        ).order_by(
            exact_rank.asc(),
            prefix_rank.asc(),
            substring_rank.asc(),
            similarity.desc(),
            WorkoutTemplate.name.asc(),
            WorkoutTemplate.id.asc(),
        )
    if equipment_ids is not None:
        if equipment_ids:
            orm_query = orm_query.filter(
                ~WorkoutTemplate.equipment_requirements.any(
                    ~WorkoutTemplateEquipment.equipment_id.in_(set(equipment_ids))
                )
            )
        else:
            orm_query = orm_query.filter(
                ~WorkoutTemplate.equipment_requirements.any()
            )
    preferences = get_training_preferences(db, user_id)
    owned = _owned_equipment_ids(db, user_id)
    if recommended:
        required_count = (
            select(func.count(WorkoutTemplateEquipment.equipment_id))
            .where(WorkoutTemplateEquipment.template_id == WorkoutTemplate.id)
            .correlate(WorkoutTemplate)
            .scalar_subquery()
        )
        if owned:
            covered_count = (
                select(func.count(WorkoutTemplateEquipment.equipment_id))
                .where(
                    WorkoutTemplateEquipment.template_id == WorkoutTemplate.id,
                    WorkoutTemplateEquipment.equipment_id.in_(owned),
                )
                .correlate(WorkoutTemplate)
                .scalar_subquery()
            )
        else:
            covered_count = 0
        score = case((required_count == 0, 20.0), else_=20.0 * covered_count / required_count)
        possible = 20.0
        goal = preferences.get("fitnessGoal")
        if goal:
            score += case((WorkoutTemplate.goal == goal, 30.0), else_=0.0)
            possible += 30.0
        level = preferences.get("experienceLevel")
        if level:
            levels = ["no-experience", "beginner", "intermediate", "advanced"]
            adjacent = []
            if level in levels:
                index = levels.index(level)
                adjacent = levels[max(0, index - 1) : index] + levels[index + 1 : index + 2]
            score += case(
                (WorkoutTemplate.level == level, 20.0),
                (WorkoutTemplate.level.in_(adjacent), 10.0),
                else_=0.0,
            )
            possible += 20.0
        frequency_preference = _frequency_preference(preferences)
        if frequency_preference:
            score += case(
                (WorkoutTemplate.frequency == frequency_preference, 20.0),
                (func.abs(WorkoutTemplate.frequency - frequency_preference) == 1, 10.0),
                else_=0.0,
            )
            possible += 20.0
        total = min(orm_query.order_by(None).count(), 3)
        page_limit = min(limit, max(0, total - offset))
        templates = (
            orm_query.order_by(
                (score / possible).desc(),
                WorkoutTemplate.name.asc(),
                WorkoutTemplate.id.asc(),
            )
            .offset(offset)
            .limit(page_limit)
            .all()
            if page_limit
            else []
        )
    elif not query:
        total = orm_query.order_by(None).count()
        templates = (
            orm_query.order_by(
                WorkoutTemplate.featured_rank.is_(None),
                WorkoutTemplate.featured_rank.asc(),
                WorkoutTemplate.name.asc(),
                WorkoutTemplate.id.asc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
    else:
        total = orm_query.order_by(None).count()
        templates = orm_query.offset(offset).limit(limit).all()
    summaries = [
        _summary(
            item,
            owned_equipment_ids=owned,
            recommendation=_recommendation(item, preferences, owned),
        )
        for item in templates
    ]
    return summaries, total


def _validate_template_exercises(db: Session, user_id: str, payload: WorkoutTemplateWrite) -> None:
    for day in payload.days:
        for item in day.exercises:
            if get_exercise(db, item.exercise_id, user_id=user_id) is None:
                raise TemplateValidationError(f"Exercise '{item.exercise_id}' is unavailable")


def _replace_template_graph(
    db: Session, template: WorkoutTemplate, user_id: str, payload: WorkoutTemplateWrite
) -> None:
    _validate_template_exercises(db, user_id, payload)
    template.name = payload.name.strip()
    template.description = payload.description.strip()
    template.goal = payload.goal
    template.level = payload.level
    template.frequency = len(payload.days)
    template.duration_minutes = payload.duration_minutes
    template.days.clear()
    template.aliases.clear()
    template.equipment_requirements.clear()
    db.flush()
    equipment: set[str] = set()
    for day_position, day_input in enumerate(payload.days):
        day = WorkoutTemplateDay(
            position=day_position,
            title=day_input.title.strip(),
            day_type=day_input.day_type.strip(),
        )
        for position, item in enumerate(day_input.exercises):
            exercise = get_exercise(db, item.exercise_id, user_id=user_id)
            equipment.update(entry.id for entry in exercise.equipment)
            day.exercises.append(
                WorkoutTemplateExercise(
                    position=position,
                    exercise_id=item.exercise_id,
                    block_type=item.block_type,
                    sets=item.sets,
                    reps_min=item.reps_min,
                    reps_max=item.reps_max,
                    rest_seconds=item.rest_seconds,
                    cue=item.cue.strip() if item.cue else None,
                )
            )
        template.days.append(day)
    aliases = {alias.strip().lower() for alias in payload.aliases if alias.strip()}
    template.aliases.extend(WorkoutTemplateAlias(alias=alias) for alias in sorted(aliases))
    template.equipment_requirements.extend(
        WorkoutTemplateEquipment(equipment_id=equipment_id) for equipment_id in sorted(equipment)
    )


def create_template(db: Session, user_id: str, payload: WorkoutTemplateWrite) -> WorkoutTemplate:
    template = WorkoutTemplate(owner_user_id=user_id, scope="private")
    try:
        _replace_template_graph(db, template, user_id, payload)
        db.add(template)
        db.commit()
        return get_template(db, user_id, template.id)
    except Exception:
        db.rollback()
        raise


def update_template(
    db: Session, user_id: str, template_id: str, payload: WorkoutTemplateWrite, version: int
) -> WorkoutTemplate:
    template = (
        _template_query(db, user_id)
        .filter(WorkoutTemplate.id == template_id)
        .with_for_update()
        .first()
    )
    if template is None:
        raise TemplateNotFoundError("Workout template not found")
    if template.scope != "private" or template.owner_user_id != user_id:
        raise TemplateConflictError("System templates are read-only; customize a copy instead")
    if template.version != version:
        raise TemplateConflictError("This program changed elsewhere; reload the latest version")
    try:
        _replace_template_graph(db, template, user_id, payload)
        template.version += 1
        db.commit()
        return get_template(db, user_id, template.id)
    except Exception:
        db.rollback()
        raise


def archive_template(db: Session, user_id: str, template_id: str) -> None:
    lock_user_schedule(db, user_id)
    template = (
        _template_query(db, user_id)
        .populate_existing()
        .filter(WorkoutTemplate.id == template_id)
        .with_for_update()
        .first()
    )
    if template is None:
        raise TemplateNotFoundError("Workout template not found")
    if template.scope != "private" or template.owner_user_id != user_id:
        raise TemplateConflictError("System templates cannot be archived")
    referenced = (
        db.query(UserProgramActivation.id)
        .filter(
            UserProgramActivation.user_id == user_id,
            UserProgramActivation.template_id == template_id,
            UserProgramActivation.status.in_(["active", "scheduled"]),
        )
        .with_for_update()
        .first()
    )
    if referenced:
        raise TemplateConflictError(
            "This program is active or scheduled. Deactivate it before archiving."
        )
    template.is_archived = True
    db.commit()


def clone_template(db: Session, user_id: str, template_id: str) -> WorkoutTemplate:
    source = get_template(db, user_id, template_id)
    detail = template_detail(source, db=db, user_id=user_id)
    payload = WorkoutTemplateWrite.model_validate(
        {
            "name": f"{source.name} Copy",
            "description": source.description,
            "goal": source.goal,
            "level": source.level,
            "durationMinutes": source.duration_minutes,
            "aliases": [item.alias for item in source.aliases],
            "days": [
                {
                    "title": day.title,
                    "dayType": day.day_type,
                    "exercises": [
                        {
                            "exerciseId": item.exercise_id,
                            "blockType": item.block_type,
                            "sets": item.sets,
                            "repsMin": item.reps_min,
                            "repsMax": item.reps_max,
                            "restSeconds": item.rest_seconds,
                            "cue": item.cue,
                        }
                        for item in day.exercises
                    ],
                }
                for day in detail.days
            ],
        }
    )
    return create_template(db, user_id, payload)


def list_substitutions(
    db: Session, user_id: str, exercise_id: str, *, owned_only: bool = False
) -> list[Exercise]:
    original = get_exercise(db, exercise_id, user_id=user_id)
    if original is None:
        raise TemplateNotFoundError("Exercise not found")
    owned = _owned_equipment_ids(db, user_id)
    candidates = (
        db.query(Exercise)
        .options(selectinload(Exercise.equipment))
        .filter(
            Exercise.id != exercise_id,
            Exercise.is_active.is_(True),
            Exercise.exercise_type == original.exercise_type,
            Exercise.primary_muscle == original.primary_muscle,
            exercise_visibility_filter(user_id),
        )
        .all()
    )
    if owned_only:
        candidates = [
            item for item in candidates if {eq.id for eq in item.equipment} <= owned
        ]
    original_secondary = set(original.secondary_muscles or [])
    candidates.sort(
        key=lambda item: (
            item.exercise_type != original.exercise_type,
            item.primary_muscle != original.primary_muscle,
            -len(original_secondary & set(item.secondary_muscles or [])),
            item.id,
        )
    )
    return candidates[:20]


def _validate_weekdays(frequency: int, weekdays: list[int]) -> list[int]:
    normalized = sorted(set(weekdays))
    if len(normalized) != frequency or any(day < 0 or day > 6 for day in normalized):
        raise TemplateValidationError(
            f"Choose exactly {frequency} distinct weekdays between Monday (0) and Sunday (6)"
        )
    return normalized


def validate_activation_schedule_input(
    *,
    week_start: date,
    effective_date: date,
    apply_mode: Optional[str],
) -> tuple[date, date, str]:
    """Validate the shared preview/activation calendar contract."""
    from app.core.workout_week_service import get_week_start

    base_week = get_week_start(week_start)
    server_today = datetime.now(timezone.utc).date()
    allowed_base_weeks = {
        get_week_start(server_today + timedelta(days=offset)) for offset in (-1, 0, 1)
    }
    if base_week not in allowed_base_weeks:
        raise TemplateValidationError("weekStart must identify the caller's current week")

    resolved_mode = apply_mode
    if resolved_mode is None:
        resolved_mode = (
            "next-week"
            if effective_date == base_week + timedelta(days=7)
            else "now"
        )
    if resolved_mode == "next-week":
        if effective_date != base_week + timedelta(days=7):
            raise TemplateValidationError(
                "Next-week program changes must take effect on the following Monday"
            )
        return base_week, base_week + timedelta(days=7), resolved_mode
    if resolved_mode != "now":
        raise TemplateValidationError("applyMode must be 'now' or 'next-week'")
    if not base_week <= effective_date <= base_week + timedelta(days=6):
        raise TemplateValidationError(
            "Immediate program activation must take effect within the requested week"
        )
    return base_week, base_week, resolved_mode


def validate_caller_local_today(value: date) -> date:
    """Accept the caller's local date without allowing arbitrary time travel."""
    server_today = datetime.now(timezone.utc).date()
    if value not in {
        server_today - timedelta(days=1),
        server_today,
        server_today + timedelta(days=1),
    }:
        raise TemplateValidationError(
            "effectiveDate must be the caller's current local date"
        )
    return value


def _activation_request_fingerprint(
    *,
    template_id: str,
    template_version: int,
    week_start: date,
    effective_date: date,
    weekdays: list[int],
    substitutions: dict[str, str],
    apply_mode: str,
) -> str:
    from app.core.workout_week_service import get_week_start

    canonical = {
        "applyMode": apply_mode,
        "effectiveDate": effective_date.isoformat(),
        "substitutions": dict(sorted(substitutions.items())),
        "templateId": template_id,
        "templateVersion": template_version,
        "weekStart": get_week_start(week_start).isoformat(),
        "weekdays": sorted(set(weekdays)),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _session_statuses_for_workouts(
    db: Session, user_id: str, workouts: list[dict]
) -> dict[str, str]:
    workout_day_ids = {
        workout.get("workoutDayId")
        for workout in workouts
        if isinstance(workout.get("workoutDayId"), str)
    }
    if not workout_day_ids:
        return {}
    return dict(
        db.query(WorkoutSession.workout_day_id, WorkoutSession.status)
        .filter(
            WorkoutSession.user_id == user_id,
            WorkoutSession.workout_day_id.in_(workout_day_ids),
            WorkoutSession.status.in_(["completed", "in_progress"]),
        )
        .all()
    )


def preview_activation(
    db: Session,
    user_id: str,
    template_id: str,
    *,
    week_start: date,
    effective_date: date,
    weekdays: list[int],
    apply_mode: Optional[str] = None,
    template: Optional[WorkoutTemplate] = None,
) -> tuple[WorkoutTemplate, list[int], ScheduleImpactOut, list[EquipmentConflictOut]]:
    _, target_week, _ = validate_activation_schedule_input(
        week_start=week_start,
        effective_date=effective_date,
        apply_mode=apply_mode,
    )
    template = template or get_template(db, user_id, template_id)
    weekdays = _validate_weekdays(template.frequency, weekdays)
    plan = (
        db.query(WorkoutWeekPlan)
        .filter_by(user_id=user_id, week_start_date=target_week)
        .first()
    )
    plan_workouts = list(plan.plan_json.get("workouts", [])) if plan else []
    statuses = _session_statuses_for_workouts(db, user_id, plan_workouts)
    preserved: list[str] = []
    replaced: list[str] = []
    occupied_dates: set[date] = set()
    for workout in plan_workouts:
        workout_date = date.fromisoformat(workout["date"])
        workout_id = workout["workoutDayId"]
        if workout_date < effective_date or statuses.get(workout_id) in {"completed", "in_progress"}:
            preserved.append(workout_id)
            occupied_dates.add(workout_date)
        else:
            replaced.append(workout_id)
    scheduled = [target_week + timedelta(days=value) for value in weekdays]
    scheduled = [
        value
        for value in scheduled
        if value >= effective_date and value not in occupied_dates
    ]

    owned = _owned_equipment_ids(db, user_id)
    conflicts: list[EquipmentConflictOut] = []
    for day in template.days:
        day_exercise_ids = {entry.exercise_id for entry in day.exercises}
        suggested_in_day: set[str] = set()
        for item in day.exercises:
            if not item.exercise.is_active or (
                item.exercise.source == "user" and item.exercise.owner_user_id != user_id
            ):
                raise TemplateValidationError(
                    f"Exercise '{item.exercise_id}' is no longer available; edit the program before activation"
                )
            required = {entry.id for entry in item.exercise.equipment}
            missing = required - owned
            if not missing:
                continue
            alternatives = [
                candidate
                for candidate in list_substitutions(
                    db, user_id, item.exercise_id, owned_only=True
                )
                if candidate.id not in day_exercise_ids
                and candidate.id not in suggested_in_day
            ]
            if alternatives:
                suggested_in_day.add(alternatives[0].id)
            conflicts.append(
                EquipmentConflictOut(
                    templateDayId=day.id,
                    templateExerciseId=item.id,
                    originalExerciseId=item.exercise_id,
                    missingEquipmentIds=sorted(missing),
                    suggestedExerciseId=alternatives[0].id if alternatives else None,
                    alternativeExerciseIds=[entry.id for entry in alternatives],
                    suggestedExercise=(
                        ExerciseOut.model_validate(
                            exercise_to_dict(alternatives[0], user_id=user_id)
                        )
                        if alternatives
                        else None
                    ),
                    alternatives=[
                        ExerciseOut.model_validate(
                            exercise_to_dict(entry, user_id=user_id)
                        )
                        for entry in alternatives
                    ],
                )
            )
    return template, weekdays, ScheduleImpactOut(
        preservedWorkoutDayIds=preserved,
        replacedWorkoutDayIds=replaced,
        scheduledDates=scheduled,
    ), conflicts


def _activation_snapshot(
    template: WorkoutTemplate, user_id: str, substitutions: dict[str, str], db: Session
) -> dict:
    detail = template_detail(template, db=db, user_id=user_id).model_dump(
        by_alias=True, mode="json"
    )
    owned_equipment_ids = _owned_equipment_ids(db, user_id)
    for day in detail["days"]:
        for item in day["exercises"]:
            replacement_id = substitutions.get(item["id"])
            if not replacement_id:
                continue
            replacement = get_exercise(db, replacement_id, user_id=user_id)
            if replacement is None:
                raise TemplateValidationError(f"Replacement exercise '{replacement_id}' is unavailable")
            required_equipment = {entry.id for entry in replacement.equipment}
            if not required_equipment <= owned_equipment_ids:
                raise TemplateValidationError(
                    "Replacement exercises must use equipment you own"
                )
            if replacement.exercise_type != item["exercise"]["exercise_type"]:
                raise TemplateValidationError(
                    "Replacement exercises must have the same exercise type"
                )
            if replacement.primary_muscle != item["exercise"]["primary_muscle"]:
                raise TemplateValidationError(
                    "Replacement exercises must train the same primary muscle"
                )
            item["originalExerciseId"] = item["exerciseId"]
            item["exerciseId"] = replacement.id
            item["exercise"] = ExerciseOut.model_validate(
                exercise_to_dict(replacement, user_id=user_id)
            ).model_dump(mode="json")
        resolved_ids = [item["exerciseId"] for item in day["exercises"]]
        if len(resolved_ids) != len(set(resolved_ids)):
            raise TemplateValidationError(
                "Exercise replacements may not duplicate another exercise in the same day"
            )
    return detail


def _validate_activation_substitutions(
    db: Session,
    user_id: str,
    template: WorkoutTemplate,
    conflicts: list[EquipmentConflictOut],
    substitutions: dict[str, str],
) -> None:
    conflict_ids = {item.template_exercise_id for item in conflicts}
    supplied_ids = set(substitutions)
    if supplied_ids != conflict_ids:
        if conflict_ids - supplied_ids:
            raise TemplateValidationError(
                "Review every equipment replacement before activation"
            )
        raise TemplateValidationError(
            "Substitutions may only target equipment conflicts from the activation preview"
        )

    occurrences = {
        item.id: (day.id, item)
        for day in template.days
        for item in day.exercises
    }
    owned = _owned_equipment_ids(db, user_id)
    resolved_by_day: dict[str, list[str]] = {}
    for day in template.days:
        resolved_by_day[day.id] = [item.exercise_id for item in day.exercises]

    for occurrence_id, replacement_id in substitutions.items():
        day_id, occurrence = occurrences[occurrence_id]
        replacement = get_exercise(db, replacement_id, user_id=user_id)
        if replacement is None:
            raise TemplateValidationError(
                f"Replacement exercise '{replacement_id}' is unavailable"
            )
        if replacement.exercise_type != occurrence.exercise.exercise_type:
            raise TemplateValidationError(
                "Replacement exercises must have the same exercise type"
            )
        if replacement.primary_muscle != occurrence.exercise.primary_muscle:
            raise TemplateValidationError(
                "Replacement exercises must train the same primary muscle"
            )
        if not {entry.id for entry in replacement.equipment} <= owned:
            raise TemplateValidationError(
                "Replacement exercises must use equipment you own"
            )
        resolved_by_day[day_id][occurrence.position] = replacement.id

    if any(len(ids) != len(set(ids)) for ids in resolved_by_day.values()):
        raise TemplateValidationError(
            "Exercise replacements may not duplicate another exercise in the same day"
        )


def active_program_to_out(
    activation: UserProgramActivation, *, status: Optional[str] = None
) -> ActiveProgramOut:
    return ActiveProgramOut(
        id=activation.id,
        templateId=activation.template_id,
        templateVersion=activation.template_version,
        templateName=activation.activation_snapshot.get("name", "Program"),
        status=status or activation.status,
        effectiveDate=activation.effective_date,
        weekdays=list(activation.weekdays),
        applyMode=activation.apply_mode,
        requiresReview=activation.requires_review,
        createdAt=activation.created_at,
    )


def get_active_program(db: Session, user_id: str) -> Optional[UserProgramActivation]:
    return (
        db.query(UserProgramActivation)
        .filter(
            UserProgramActivation.user_id == user_id,
            UserProgramActivation.status == "active",
        )
        .first()
    )


def get_scheduled_program(db: Session, user_id: str) -> Optional[UserProgramActivation]:
    return (
        db.query(UserProgramActivation)
        .filter(
            UserProgramActivation.user_id == user_id,
            UserProgramActivation.status == "scheduled",
        )
        .order_by(
            UserProgramActivation.effective_date.asc(),
            UserProgramActivation.created_at.desc(),
        )
        .first()
    )


def get_effective_program(
    db: Session, user_id: str, target_date: date
) -> Optional[UserProgramActivation]:
    return (
        db.query(UserProgramActivation)
        .filter(
            UserProgramActivation.user_id == user_id,
            UserProgramActivation.status.in_(["active", "scheduled"]),
            UserProgramActivation.effective_date <= target_date,
        )
        .order_by(
            UserProgramActivation.effective_date.desc(),
            UserProgramActivation.created_at.desc(),
        )
        .first()
    )


def get_program_view(
    db: Session, user_id: str, effective_date: Optional[date]
) -> tuple[Optional[UserProgramActivation], Optional[UserProgramActivation]]:
    target_date = (
        validate_caller_local_today(effective_date)
        if effective_date is not None
        else datetime.now(timezone.utc).date()
    )
    current = get_effective_program(db, user_id, target_date)
    scheduled = (
        db.query(UserProgramActivation)
        .filter(
            UserProgramActivation.user_id == user_id,
            UserProgramActivation.status.in_(["active", "scheduled"]),
            UserProgramActivation.effective_date > target_date,
        )
        .order_by(
            UserProgramActivation.effective_date.asc(),
            UserProgramActivation.created_at.desc(),
        )
        .first()
    )
    return current, scheduled


def _materialize_programs_through(
    db: Session, user_id: str, effective_date: date
) -> Optional[UserProgramActivation]:
    """Update stored statuses inside an existing schedule mutation transaction."""
    due = (
        db.query(UserProgramActivation)
        .filter(
            UserProgramActivation.user_id == user_id,
            UserProgramActivation.status == "scheduled",
            UserProgramActivation.effective_date <= effective_date,
        )
        .order_by(
            UserProgramActivation.effective_date.desc(),
            UserProgramActivation.created_at.desc(),
        )
        .with_for_update()
        .all()
    )
    if not due:
        return get_active_program(db, user_id)
    promoted = due[0]
    for activation in db.query(UserProgramActivation).filter(
        UserProgramActivation.user_id == user_id,
        UserProgramActivation.status == "active",
    ):
        activation.status = "superseded"
    for activation in due[1:]:
        activation.status = "superseded"
    db.flush()
    promoted.status = "active"
    db.flush()
    return promoted


def activate_program(
    db: Session,
    user_id: str,
    template_id: str,
    *,
    template_version: int,
    week_start: date,
    effective_date: date,
    weekdays: list[int],
    substitutions: dict[str, str],
    apply_mode: str,
    client_operation_id: str,
) -> tuple[UserProgramActivation, object]:
    request_fingerprint = _activation_request_fingerprint(
        template_id=template_id,
        template_version=template_version,
        week_start=week_start,
        effective_date=effective_date,
        weekdays=weekdays,
        substitutions=substitutions,
        apply_mode=apply_mode,
    )
    lock_user_schedule(db, user_id)
    existing = (
        db.query(UserProgramActivation)
        .filter_by(user_id=user_id, client_operation_id=client_operation_id)
        .first()
    )
    if existing:
        if existing.request_fingerprint != request_fingerprint:
            raise TemplateConflictError(
                "clientOperationId was already used for a different activation request"
            )
        return existing, _activation_week_response(db, existing)
    base_week, target_week, _ = validate_activation_schedule_input(
        week_start=week_start,
        effective_date=effective_date,
        apply_mode=apply_mode,
    )
    _materialize_programs_through(
        db,
        user_id,
        effective_date if apply_mode == "now" else base_week,
    )
    template = (
        _template_query(db, user_id)
        .populate_existing()
        .filter(WorkoutTemplate.id == template_id)
        .with_for_update()
        .first()
    )
    if template is None:
        raise TemplateNotFoundError("Workout template not found")
    if template.version != template_version:
        raise TemplateConflictError("This program changed elsewhere; reload the latest version")
    template, normalized, _, conflicts = preview_activation(
        db,
        user_id,
        template_id,
        week_start=week_start,
        effective_date=effective_date,
        weekdays=weekdays,
        apply_mode=apply_mode,
        template=template,
    )
    _validate_activation_substitutions(
        db, user_id, template, conflicts, substitutions
    )
    status = "scheduled" if apply_mode == "next-week" else "active"
    statuses_to_replace = ["active", "scheduled"] if apply_mode == "now" else ["scheduled"]
    try:
        for current in (
            db.query(UserProgramActivation)
            .filter(
                UserProgramActivation.user_id == user_id,
                UserProgramActivation.status.in_(statuses_to_replace),
            )
            .with_for_update()
            .all()
        ):
            current.status = "superseded"
        activation = UserProgramActivation(
            user_id=user_id,
            template_id=template.id,
            template_version=template.version,
            status=status,
            effective_date=effective_date,
            weekdays=normalized,
            activation_snapshot=_activation_snapshot(template, user_id, substitutions, db),
            apply_mode=apply_mode,
            client_operation_id=client_operation_id,
            request_fingerprint=request_fingerprint,
        )
        db.add(activation)
        db.flush()
        plan = install_activation_week(db, activation, target_week, commit=False)
        activation.activation_snapshot = {
            **activation.activation_snapshot,
            "installedWeekPlan": plan.model_dump(by_alias=True, mode="json"),
        }
        flag_modified(activation, "activation_snapshot")
        db.commit()
    except IntegrityError as error:
        db.rollback()
        replay = (
            db.query(UserProgramActivation)
            .filter_by(user_id=user_id, client_operation_id=client_operation_id)
            .first()
        )
        if replay:
            if replay.request_fingerprint != request_fingerprint:
                raise TemplateConflictError(
                    "clientOperationId was already used for a different activation request"
                )
            return replay, _activation_week_response(db, replay)
        raise TemplateConflictError(
            "Another program activation completed first; reload your active program"
        ) from error
    db.refresh(activation)
    return activation, plan


def _activation_week_response(
    db: Session, activation: UserProgramActivation
):
    from app.core.workout_week_service import _plan_json_to_response, get_week_start

    installed = activation.activation_snapshot.get("installedWeekPlan")
    if installed:
        return _plan_json_to_response(installed)
    target_week = get_week_start(activation.effective_date)
    plan = (
        db.query(WorkoutWeekPlan)
        .filter_by(user_id=activation.user_id, week_start_date=target_week)
        .first()
    )
    if plan is None or plan.plan_json.get("programActivationId") != activation.id:
        raise TemplateConflictError(
            "The activation exists but its installed workout week is unavailable"
        )
    return _plan_json_to_response(plan.plan_json)


def _workout_from_snapshot(
    activation: UserProgramActivation,
    day: dict,
    workout_date: date,
    slot_index: int,
) -> dict:
    blocks: dict[str, list] = {"main": [], "accessory": []}
    for item in day["exercises"]:
        block = item.get("blockType", "main")
        blocks.setdefault(block, []).append(
            {
                "exercise": item["exercise"],
                "prescription": {
                    "sets": item["sets"],
                    "repsMin": item["repsMin"],
                    "repsMax": item["repsMax"],
                    "restSeconds": item["restSeconds"],
                },
                "exerciseRationale": item.get("cue"),
            }
        )
    workout_day_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"primerep:{activation.id}:{workout_date.isoformat()}:{day['id']}")
    )
    return {
        "workoutDayId": workout_day_id,
        "slotIndex": slot_index,
        "date": workout_date.isoformat(),
        "durationMinutes": activation.activation_snapshot["durationMinutes"],
        "title": day["title"],
        "splitKey": "program",
        "dayType": day["dayType"],
        "estimatedMinutes": activation.activation_snapshot["durationMinutes"],
        "workoutIntent": activation.activation_snapshot.get("description") or None,
        "deferredMuscles": [],
        "unavailableMuscles": [],
        "exerciseBlocks": [
            {"blockType": name, "items": items}
            for name, items in blocks.items()
            if items
        ],
        "programActivationId": activation.id,
        "templateId": activation.template_id,
        "templateVersion": activation.template_version,
        "templateDayId": day["id"],
        "templateDayPosition": day["position"],
    }


def _template_day_index(days: list[dict], workout: dict) -> Optional[int]:
    position = workout.get("templateDayPosition")
    if isinstance(position, int):
        for index, day in enumerate(days):
            if day.get("position") == position:
                return index
    day_id = workout.get("templateDayId")
    return next(
        (index for index, day in enumerate(days) if day.get("id") == day_id),
        None,
    )


def _next_template_day_index(
    days: list[dict], workouts: list[dict], template_id: Optional[str]
) -> Optional[int]:
    matching = [
        workout
        for workout in workouts
        if workout.get("templateId") == template_id
        and _template_day_index(days, workout) is not None
    ]
    if not matching:
        return None
    latest = max(
        matching,
        key=lambda item: (item.get("date", ""), item.get("slotIndex", 0)),
    )
    latest_index = _template_day_index(days, latest)
    return (latest_index + 1) % len(days) if latest_index is not None else None


def _activation_start_day_index(
    db: Session,
    activation: UserProgramActivation,
    week_start: date,
    days: list[dict],
    preserved: list[dict],
) -> int:
    continued = _next_template_day_index(days, preserved, activation.template_id)
    if continued is not None:
        return continued

    prior_plans = (
        db.query(WorkoutWeekPlan)
        .filter(
            WorkoutWeekPlan.user_id == activation.user_id,
            WorkoutWeekPlan.week_start_date < week_start,
        )
        .order_by(WorkoutWeekPlan.week_start_date.desc())
        .all()
    )
    for prior in prior_plans:
        if prior.plan_json.get("templateId") != activation.template_id:
            continue
        next_position = prior.plan_json.get("nextTemplateDayPosition")
        if isinstance(next_position, int):
            positioned = next(
                (
                    index
                    for index, day in enumerate(days)
                    if day.get("position") == next_position
                ),
                None,
            )
            if positioned is not None:
                return positioned
        next_day_id = prior.plan_json.get("nextTemplateDayId")
        matched = next(
            (index for index, day in enumerate(days) if day.get("id") == next_day_id),
            None,
        )
        if matched is not None:
            return matched
        continued = _next_template_day_index(
            days, prior.plan_json.get("workouts", []), activation.template_id
        )
        if continued is not None:
            return continued
    return 0


def install_activation_week(
    db: Session,
    activation: UserProgramActivation,
    week_start: date,
    *,
    commit: bool = True,
):
    from app.core.workout_week_service import _plan_json_to_response, get_week_start

    week_start = get_week_start(week_start)
    lock_user_schedule(db, activation.user_id)
    record = (
        db.query(WorkoutWeekPlan)
        .filter_by(user_id=activation.user_id, week_start_date=week_start)
        .first()
    )
    server_today = datetime.now(timezone.utc).date()
    earliest_current_week = min(
        get_week_start(server_today + timedelta(days=offset)) for offset in (-1, 0, 1)
    )
    if (
        record
        and record.plan_json.get("programActivationId")
        and week_start < earliest_current_week
    ):
        return _plan_json_to_response(record.plan_json)
    current = list(record.plan_json.get("workouts", [])) if record else []
    statuses = _session_statuses_for_workouts(db, activation.user_id, current)
    preserved = [
        workout
        for workout in current
        if date.fromisoformat(workout["date"]) < activation.effective_date
        or statuses.get(workout["workoutDayId"]) in {"completed", "in_progress"}
    ]
    occupied_dates = {date.fromisoformat(workout["date"]) for workout in preserved}
    days = sorted(activation.activation_snapshot["days"], key=lambda item: item["position"])
    start_day_index = _activation_start_day_index(
        db, activation, week_start, days, preserved
    )
    generated = []
    eligible_dates = []
    for weekday in activation.weekdays:
        workout_date = week_start + timedelta(days=weekday)
        if workout_date < activation.effective_date or workout_date in occupied_dates:
            continue
        eligible_dates.append(workout_date)
    for index, workout_date in enumerate(eligible_dates):
        day = days[(start_day_index + index) % len(days)]
        generated.append(_workout_from_snapshot(activation, day, workout_date, 0))
    workouts = sorted(preserved + generated, key=lambda item: (item["date"], item.get("slotIndex", 0)))
    for position, workout in enumerate(workouts):
        workout["slotIndex"] = position
    plan_json = {
        "weekStart": week_start.isoformat(),
        "daysPerWeek": len(workouts),
        "onboardingWorkoutContractVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "seed": f"program:{activation.id}:{week_start.isoformat()}",
        "programActivationId": activation.id,
        "templateId": activation.template_id,
        "templateVersion": activation.template_version,
        "nextTemplateDayId": days[
            (start_day_index + len(generated)) % len(days)
        ]["id"],
        "nextTemplateDayPosition": days[
            (start_day_index + len(generated)) % len(days)
        ]["position"],
        "workouts": workouts,
    }
    if record and record.plan_json.get("completedWorkoutSnapshots"):
        plan_json["completedWorkoutSnapshots"] = record.plan_json[
            "completedWorkoutSnapshots"
        ]
    if record:
        record.plan_json = plan_json
        record.days_per_week = len(workouts)
        flag_modified(record, "plan_json")
    else:
        record = WorkoutWeekPlan(
            user_id=activation.user_id,
            week_start_date=week_start,
            days_per_week=len(workouts),
            plan_json=plan_json,
        )
        db.add(record)
    db.flush()
    if commit:
        db.commit()
    return _plan_json_to_response(plan_json)


def skip_program_workout_day(
    db: Session, plan: WorkoutWeekPlan, workout_day_id: str
):
    from app.core.workout_week_service import _plan_json_to_response

    activation_id = plan.plan_json.get("programActivationId")
    activation = db.get(UserProgramActivation, activation_id) if activation_id else None
    if activation is None:
        return None
    lock_user_schedule(db, activation.user_id)
    original_workouts = [dict(workout) for workout in plan.plan_json.get("workouts", [])]
    statuses = _session_statuses_for_workouts(db, activation.user_id, original_workouts)
    workouts = sorted(
        original_workouts,
        key=lambda item: (item["date"], item.get("slotIndex", 0)),
    )
    removed_index = next(
        (index for index, workout in enumerate(workouts) if workout.get("workoutDayId") == workout_day_id),
        None,
    )
    if removed_index is None:
        return None
    removed = workouts.pop(removed_index)
    protected_ids = {
        workout.get("workoutDayId")
        for workout in workouts
        if statuses.get(workout.get("workoutDayId")) in {"completed", "in_progress"}
    }
    occupied_dates = {
        date.fromisoformat(workout["date"])
        for workout in workouts
        if workout.get("workoutDayId") in protected_ids
    }
    selected_dates = [
        plan.week_start_date + timedelta(days=weekday)
        for weekday in sorted(set(activation.weekdays))
    ]
    scheduled: list[dict] = []
    last_assigned = plan.week_start_date - timedelta(days=1)
    for index, workout in enumerate(workouts):
        if workout.get("workoutDayId") in protected_ids:
            workout_date = date.fromisoformat(workout["date"])
            scheduled.append(workout)
            last_assigned = workout_date
            continue
        next_protected_date = next(
            (
                date.fromisoformat(later["date"])
                for later in workouts[index + 1 :]
                if later.get("workoutDayId") in protected_ids
            ),
            None,
        )
        candidate = next(
            (
                value
                for value in selected_dates
                if value > last_assigned
                and value not in occupied_dates
                and (next_protected_date is None or value < next_protected_date)
            ),
            None,
        )
        if candidate is None:
            continue
        workout["date"] = candidate.isoformat()
        occupied_dates.add(candidate)
        scheduled.append(workout)
        last_assigned = candidate

    workouts = scheduled
    days = sorted(activation.activation_snapshot["days"], key=lambda item: item["position"])
    last_workout = workouts[-1] if workouts else removed
    last_index = _template_day_index(days, last_workout)
    last_index = last_index if last_index is not None else -1
    next_day = days[(last_index + 1) % len(days)]
    last_date = max(occupied_dates, default=plan.week_start_date - timedelta(days=1))
    replacement_date = next(
        (
            candidate
            for candidate in selected_dates
            if candidate > last_date and candidate not in occupied_dates
        ),
        None,
    )
    if replacement_date is not None:
        replacement = _workout_from_snapshot(
            activation,
            next_day,
            replacement_date,
            len(workouts),
        )
        replacement["workoutDayId"] = str(uuid.uuid4())
        workouts.append(replacement)
    workouts.sort(key=lambda item: (item["date"], item.get("slotIndex", 0)))
    for index, workout in enumerate(workouts):
        workout["slotIndex"] = index
    data = dict(plan.plan_json)
    data["workouts"] = workouts
    data["daysPerWeek"] = len(workouts)
    next_index = _next_template_day_index(days, workouts, activation.template_id)
    data["nextTemplateDayId"] = days[next_index or 0]["id"]
    data["nextTemplateDayPosition"] = days[next_index or 0]["position"]
    data["generatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    plan.plan_json = data
    flag_modified(plan, "plan_json")
    db.flush()
    future_program_weeks = (
        db.query(WorkoutWeekPlan)
        .filter(
            WorkoutWeekPlan.user_id == activation.user_id,
            WorkoutWeekPlan.week_start_date > plan.week_start_date,
        )
        .order_by(WorkoutWeekPlan.week_start_date.asc())
        .all()
    )
    for future in future_program_weeks:
        if future.plan_json.get("programActivationId") == activation.id:
            install_activation_week(
                db, activation, future.week_start_date, commit=False
            )
    db.commit()
    return _plan_json_to_response(data)


def _replace_program_week_with_preferences(
    db: Session,
    user_id: str,
    *,
    week_start: date,
    effective_date: date,
    clear_future_program_weeks: bool,
    program_activation_ids: Optional[set[str]] = None,
):
    from app.core.workout_week_service import (
        _plan_json_to_response,
        get_or_create_week_plan,
        get_week_start,
    )

    week_start = get_week_start(week_start)
    lock_user_schedule(db, user_id)
    target = (
        db.query(WorkoutWeekPlan)
        .filter_by(user_id=user_id, week_start_date=week_start)
        .first()
    )
    current_workouts = list(target.plan_json.get("workouts", [])) if target else []
    statuses = _session_statuses_for_workouts(db, user_id, current_workouts)
    preserved = [
        workout
        for workout in current_workouts
        if date.fromisoformat(workout["date"]) < effective_date
        or statuses.get(workout.get("workoutDayId")) in {"completed", "in_progress"}
    ]

    if clear_future_program_weeks:
        future_program_plans = (
            db.query(WorkoutWeekPlan)
            .filter(
                WorkoutWeekPlan.user_id == user_id,
                WorkoutWeekPlan.week_start_date > week_start,
            )
            .all()
        )
        for plan in future_program_plans:
            activation_id = plan.plan_json.get("programActivationId")
            if activation_id and (
                program_activation_ids is None
                or activation_id in program_activation_ids
            ):
                db.delete(plan)

    target_activation_id = target.plan_json.get("programActivationId") if target else None
    if (
        target_activation_id
        and program_activation_ids is not None
        and target_activation_id not in program_activation_ids
    ):
        db.commit()
        return _plan_json_to_response(target.plan_json)
    if target_activation_id:
        data = dict(target.plan_json)
        for key in ("programActivationId", "templateId", "templateVersion"):
            data.pop(key, None)
        data["preferencesRefreshRequired"] = True
        target.plan_json = data
        flag_modified(target, "plan_json")
    db.flush()

    get_or_create_week_plan(
        db,
        user_id=user_id,
        week_start=week_start,
        commit=False,
    )
    target = (
        db.query(WorkoutWeekPlan)
        .filter_by(user_id=user_id, week_start_date=week_start)
        .first()
    )
    data = dict(target.plan_json)
    occupied_dates = {workout["date"] for workout in preserved}
    generated = [
        workout
        for workout in data.get("workouts", [])
        if workout["date"] not in occupied_dates
    ]
    workouts = sorted(
        preserved + generated,
        key=lambda item: (item["date"], item.get("slotIndex", 0)),
    )
    for position, workout in enumerate(workouts):
        workout["slotIndex"] = position
    data["workouts"] = workouts
    data["daysPerWeek"] = len(workouts)
    target.days_per_week = len(workouts)
    target.plan_json = data
    flag_modified(target, "plan_json")
    db.commit()
    return _plan_json_to_response(data)


def cancel_scheduled_program(
    db: Session,
    user_id: str,
    *,
    effective_date: date,
) -> bool:
    effective_date = validate_caller_local_today(effective_date)
    lock_user_schedule(db, user_id)
    scheduled = (
        db.query(UserProgramActivation)
        .filter(
            UserProgramActivation.user_id == user_id,
            UserProgramActivation.status.in_(["active", "scheduled"]),
            UserProgramActivation.effective_date > effective_date,
        )
        .order_by(
            UserProgramActivation.effective_date.asc(),
            UserProgramActivation.created_at.desc(),
        )
        .with_for_update()
        .first()
    )
    if scheduled is None:
        return False
    current = get_effective_program(db, user_id, effective_date)
    scheduled.status = "deactivated"
    scheduled.deactivated_at = datetime.now(timezone.utc)
    db.flush()
    target_week = scheduled.effective_date - timedelta(
        days=scheduled.effective_date.weekday()
    )
    if current:
        install_activation_week(db, current, target_week, commit=False)
        db.commit()
    else:
        _replace_program_week_with_preferences(
            db,
            user_id,
            week_start=target_week,
            effective_date=scheduled.effective_date,
            clear_future_program_weeks=True,
            program_activation_ids={scheduled.id},
        )
    return True


def deactivate_program(
    db: Session,
    user_id: str,
    *,
    week_start: date,
    effective_date: date,
):
    from app.core.workout_week_service import get_week_start

    normalized_week_start = get_week_start(week_start)
    server_today = datetime.now(timezone.utc).date()
    if effective_date not in {
        server_today - timedelta(days=1),
        server_today,
        server_today + timedelta(days=1),
    }:
        raise TemplateValidationError(
            "effectiveDate must be the caller's current local date"
        )
    if week_start != normalized_week_start or get_week_start(effective_date) != normalized_week_start:
        raise TemplateValidationError(
            "weekStart must be the Monday containing effectiveDate"
        )
    lock_user_schedule(db, user_id)
    now = datetime.now(timezone.utc)
    activations = (
        db.query(UserProgramActivation)
        .filter(
            UserProgramActivation.user_id == user_id,
            UserProgramActivation.status.in_(["active", "scheduled"]),
        )
        .with_for_update()
        .all()
    )
    deactivated_ids = {activation.id for activation in activations}
    for activation in activations:
        activation.status = "deactivated"
        activation.deactivated_at = now
    db.flush()
    return _replace_program_week_with_preferences(
        db,
        user_id,
        week_start=normalized_week_start,
        effective_date=effective_date,
        clear_future_program_weeks=True,
        program_activation_ids=deactivated_ids,
    )


def create_custom_exercise(db: Session, user_id: str, payload) -> Exercise:
    return _save_custom_exercise(db, user_id, None, payload)


def update_custom_exercise(db: Session, user_id: str, exercise_id: str, payload) -> Exercise:
    exercise = get_exercise(db, exercise_id, user_id=user_id)
    if not exercise or exercise.source != "user" or exercise.owner_user_id != user_id:
        raise TemplateNotFoundError("Custom exercise not found")
    result = _save_custom_exercise(db, user_id, exercise, payload, commit=False)
    _bump_referencing_template_versions(db, user_id, exercise_id)
    db.commit()
    return get_exercise(db, result.id, user_id=user_id)


def _save_custom_exercise(
    db: Session,
    user_id: str,
    exercise: Optional[Exercise],
    payload,
    *,
    commit: bool = True,
) -> Exercise:
    equipment = db.query(Equipment).filter(Equipment.id.in_(payload.equipment_ids)).all()
    if len(equipment) != len(set(payload.equipment_ids)):
        raise TemplateValidationError("One or more equipment IDs are unavailable")
    if exercise is None:
        slug = re.sub(r"[^a-z0-9]+", "-", payload.name.lower()).strip("-") or "exercise"
        exercise = Exercise(
            id=f"user-{user_id[:8]}-{slug}-{uuid.uuid4().hex[:8]}",
            source="user",
            owner_user_id=user_id,
        )
        db.add(exercise)
    exercise.name = payload.name.strip()
    exercise.exercise_type = payload.exercise_type.strip()
    exercise.primary_muscle = payload.primary_muscle.strip()
    exercise.secondary_muscles = list(dict.fromkeys(payload.secondary_muscles))
    exercise.how_to = payload.instructions.strip() if payload.instructions else None
    exercise.equipment = equipment
    exercise.is_active = True
    if commit:
        db.commit()
    else:
        db.flush()
    return get_exercise(db, exercise.id, user_id=user_id)


def _bump_referencing_template_versions(db: Session, user_id: str, exercise_id: str) -> None:
    template_ids = (
        db.query(WorkoutTemplateDay.template_id)
        .join(WorkoutTemplateExercise)
        .filter(WorkoutTemplateExercise.exercise_id == exercise_id)
    )
    templates = (
        db.query(WorkoutTemplate)
        .options(
            selectinload(WorkoutTemplate.days)
            .selectinload(WorkoutTemplateDay.exercises)
            .selectinload(WorkoutTemplateExercise.exercise)
            .selectinload(Exercise.equipment),
            selectinload(WorkoutTemplate.equipment_requirements),
        )
        .filter(
            WorkoutTemplate.owner_user_id == user_id,
            WorkoutTemplate.id.in_(template_ids),
        )
        .all()
    )
    for template in templates:
        template.version += 1
        equipment_ids = {
            equipment.id
            for day in template.days
            for item in day.exercises
            for equipment in item.exercise.equipment
        }
        template.equipment_requirements.clear()
        db.flush()
        template.equipment_requirements.extend(
            WorkoutTemplateEquipment(equipment_id=equipment_id)
            for equipment_id in sorted(equipment_ids)
        )


def archive_custom_exercise(db: Session, user_id: str, exercise_id: str) -> None:
    exercise = get_exercise(db, exercise_id, user_id=user_id)
    if not exercise or exercise.source != "user" or exercise.owner_user_id != user_id:
        raise TemplateNotFoundError("Custom exercise not found")
    exercise.is_active = False
    _bump_referencing_template_versions(db, user_id, exercise_id)
    db.commit()


def search_exercises(db: Session, user_id: str, query: str, limit: int = 20) -> list[Exercise]:
    normalized = query.strip().lower()
    alias_exact = (
        db.query(ExerciseAlias.id)
        .filter(
            ExerciseAlias.exercise_id == Exercise.id,
            func.lower(ExerciseAlias.alias) == normalized,
        )
        .exists()
    )
    alias_prefix = (
        db.query(ExerciseAlias.id)
        .filter(
            ExerciseAlias.exercise_id == Exercise.id,
            func.lower(ExerciseAlias.alias).startswith(normalized),
        )
        .exists()
    )
    alias_substring = (
        db.query(ExerciseAlias.id)
        .filter(
            ExerciseAlias.exercise_id == Exercise.id,
            func.lower(ExerciseAlias.alias).contains(normalized),
        )
        .exists()
    )
    alias_similarity = (
        db.query(func.max(func.similarity(func.lower(ExerciseAlias.alias), normalized)))
        .filter(ExerciseAlias.exercise_id == Exercise.id)
        .correlate(Exercise)
        .scalar_subquery()
    )
    similarity = func.greatest(
        func.similarity(func.lower(Exercise.name), normalized),
        func.coalesce(alias_similarity, 0.0),
    )
    name = func.lower(Exercise.name)
    exact = case((or_(name == normalized, alias_exact), 0), else_=1)
    prefix = case((or_(name.startswith(normalized), alias_prefix), 0), else_=1)
    substring = case((or_(name.contains(normalized), alias_substring), 0), else_=1)
    return (
        db.query(Exercise)
        .options(selectinload(Exercise.equipment))
        .filter(
            Exercise.is_active.is_(True),
            exercise_visibility_filter(user_id),
            or_(name.contains(normalized), alias_substring, similarity >= 0.3),
        )
        .order_by(
            exact.asc(),
            prefix.asc(),
            substring.asc(),
            similarity.desc(),
            Exercise.name.asc(),
            Exercise.id.asc(),
        )
        .limit(limit)
        .all()
    )
