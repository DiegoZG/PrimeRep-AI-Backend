from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.coach_feed_service import reconcile_after_mutation
from app.core.exercise_service import exercise_to_dict, list_favorite_ids
from app.core.security.deps import get_current_user
from app.core.workout_template_service import (
    TemplateConflictError,
    TemplateNotFoundError,
    TemplateValidationError,
    activate_program,
    active_program_to_out,
    archive_template,
    cancel_scheduled_program,
    clone_template,
    deactivate_program,
    get_program_view,
    get_template,
    list_templates,
    preview_activation,
    search_exercises,
    template_detail,
    create_template,
    update_template,
)
from app.models.user import User
from app.schemas.exercise import ExerciseOut
from app.schemas.workout_template import (
    ActivateProgramOut,
    ActivateProgramRequest,
    ActivationPreviewOut,
    ActivationPreviewRequest,
    ActiveProgramsOut,
    ExploreSearchOut,
    WorkoutTemplateDetailOut,
    WorkoutTemplateListOut,
    WorkoutTemplateUpdate,
    WorkoutTemplateWrite,
)
from app.schemas.workout_week import WorkoutWeekResponseOut


router = APIRouter(prefix="/workout-templates", tags=["workout-templates"])
explore_router = APIRouter(prefix="/explore", tags=["explore"])


def _raise_domain(error: Exception):
    if isinstance(error, TemplateNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, TemplateConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("", response_model=WorkoutTemplateListOut)
def catalog(
    scope: Optional[Literal["system", "private"]] = None,
    query: Optional[str] = Query(None, min_length=2, max_length=100),
    goal: Optional[Literal["build-muscle", "get-stronger", "general-fitness", "conditioning"]] = None,
    level: Optional[Literal["no-experience", "beginner", "intermediate", "advanced"]] = None,
    frequency: Optional[int] = Query(None, ge=1, le=7),
    max_duration: Optional[int] = Query(None, alias="maxDuration", ge=15, le=120),
    equipment: Optional[list[str]] = Query(None),
    featured: Optional[bool] = None,
    recommended: bool = False,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items, total = list_templates(
        db,
        str(current_user.id),
        scope=scope,
        query=query,
        goal=goal,
        level=level,
        frequency=frequency,
        max_duration=max_duration,
        equipment_ids=equipment,
        featured=featured,
        recommended=recommended,
        limit=limit,
        offset=offset,
    )
    return WorkoutTemplateListOut(items=items, total=total, limit=limit, offset=offset)


@router.get("/active", response_model=ActiveProgramsOut)
def active_program(
    effective_date: Optional[date] = Query(
        None,
        alias="effectiveDate",
        description="Caller-local date used to resolve current and scheduled revisions.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    try:
        current, scheduled = get_program_view(db, user_id, effective_date)
    except TemplateValidationError as error:
        _raise_domain(error)
    return ActiveProgramsOut(
        current=active_program_to_out(current, status="active") if current else None,
        scheduled=(
            active_program_to_out(scheduled, status="scheduled") if scheduled else None
        ),
    )


@router.delete("/active", response_model=WorkoutWeekResponseOut)
def remove_active_program(
    week_start: date = Query(..., alias="weekStart"),
    effective_date: date = Query(..., alias="effectiveDate"),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    try:
        week = deactivate_program(
            db,
            str(current_user.id),
            week_start=week_start,
            effective_date=effective_date,
        )
        reconcile_after_mutation(
            db,
            str(current_user.id),
            invalidate_plan_items=True,
            invalidate_program_items=True,
        )
        return week
    except TemplateValidationError as error:
        _raise_domain(error)


@router.delete("/active/scheduled", status_code=status.HTTP_204_NO_CONTENT)
def remove_scheduled_program(
    effective_date: date = Query(..., alias="effectiveDate"),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    try:
        cancel_scheduled_program(
            db,
            str(current_user.id),
            effective_date=effective_date,
        )
        reconcile_after_mutation(
            db,
            str(current_user.id),
            invalidate_program_items=True,
        )
    except TemplateValidationError as error:
        _raise_domain(error)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("", response_model=WorkoutTemplateDetailOut, status_code=201)
def create_private_program(
    payload: WorkoutTemplateWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        template = create_template(db, str(current_user.id), payload)
        return template_detail(template, db=db, user_id=str(current_user.id))
    except (TemplateValidationError, TemplateConflictError, TemplateNotFoundError) as error:
        _raise_domain(error)


@router.get("/{template_id}", response_model=WorkoutTemplateDetailOut)
def detail(
    template_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return template_detail(
            get_template(db, str(current_user.id), template_id),
            db=db,
            user_id=str(current_user.id),
        )
    except TemplateNotFoundError as error:
        _raise_domain(error)


@router.put("/{template_id}", response_model=WorkoutTemplateDetailOut)
def replace_private_program(
    template_id: str,
    payload: WorkoutTemplateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        write = WorkoutTemplateWrite.model_validate(payload.model_dump())
        template = update_template(
            db, str(current_user.id), template_id, write, payload.version
        )
        return template_detail(template, db=db, user_id=str(current_user.id))
    except (TemplateValidationError, TemplateConflictError, TemplateNotFoundError) as error:
        _raise_domain(error)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_private_program(
    template_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        archive_template(db, str(current_user.id), template_id)
    except (TemplateConflictError, TemplateNotFoundError) as error:
        _raise_domain(error)
    return Response(status_code=204)


@router.post("/{template_id}/clone", response_model=WorkoutTemplateDetailOut, status_code=201)
def clone_program(
    template_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        cloned = clone_template(db, str(current_user.id), template_id)
        return template_detail(cloned, db=db, user_id=str(current_user.id))
    except (TemplateValidationError, TemplateNotFoundError) as error:
        _raise_domain(error)


@router.post("/{template_id}/activation-preview", response_model=ActivationPreviewOut)
def activation_preview(
    template_id: str,
    payload: ActivationPreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        template, weekdays, impact, conflicts = preview_activation(
            db,
            str(current_user.id),
            template_id,
            week_start=payload.week_start,
            effective_date=payload.effective_date,
            weekdays=payload.weekdays,
            apply_mode=payload.apply_mode,
        )
        return ActivationPreviewOut(
            template=template_detail(
                template, db=db, user_id=str(current_user.id)
            ),
            weekdays=weekdays,
            scheduleImpact=impact,
            equipmentConflicts=conflicts,
            unresolvedConflictCount=sum(
                conflict.suggested_exercise_id is None for conflict in conflicts
            ),
        )
    except (TemplateValidationError, TemplateNotFoundError) as error:
        _raise_domain(error)


@router.post("/{template_id}/activate", response_model=ActivateProgramOut)
def activate(
    template_id: str,
    payload: ActivateProgramRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        activation, week = activate_program(
            db,
            str(current_user.id),
            template_id,
            template_version=payload.template_version,
            week_start=payload.week_start,
            effective_date=payload.effective_date,
            weekdays=payload.weekdays,
            substitutions=payload.substitutions,
            apply_mode=payload.apply_mode,
            client_operation_id=payload.client_operation_id,
        )
        reconcile_after_mutation(
            db,
            str(current_user.id),
            activation_ids=[activation.id],
            template_ids=[activation.template_id] if activation.template_id else [],
            invalidate_plan_items=True,
            invalidate_program_items=True,
        )
        return ActivateProgramOut(activeProgram=active_program_to_out(activation), weekPlan=week)
    except (TemplateValidationError, TemplateConflictError, TemplateNotFoundError) as error:
        _raise_domain(error)


@explore_router.get("/search", response_model=ExploreSearchOut)
def explore_search(
    query: str = Query(..., min_length=2, max_length=100),
    scope: Literal["all", "programs", "exercises"] = "all",
    limit: int = Query(20, ge=1, le=50),
    program_offset: int = Query(0, alias="programOffset", ge=0),
    exercise_offset: int = Query(0, alias="exerciseOffset", ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    programs = []
    exercises = []
    program_total = exercise_total = 0
    if scope in {"all", "programs"}:
        programs, program_total = list_templates(
            db, str(current_user.id), query=query, limit=limit, offset=program_offset
        )
    if scope in {"all", "exercises"}:
        from app.core.exercise_service import count_exercises
        exercise_total = count_exercises(db, user_id=str(current_user.id), q=query)
        rows = search_exercises(db, str(current_user.id), query, limit=limit, offset=exercise_offset)
        favorite_ids = list_favorite_ids(
            db, str(current_user.id), [exercise.id for exercise in rows]
        )
        exercises = [
            ExerciseOut.model_validate(
                exercise_to_dict(
                    exercise,
                    user_id=str(current_user.id),
                    favorited=exercise.id in favorite_ids,
                )
            )
            for exercise in rows
        ]
    if program_total + exercise_total == 0:
        import logging
        logging.getLogger("primerep.catalog").info("catalog_zero_results", extra={"catalog_endpoint": "explore"})
    return ExploreSearchOut(programs=programs, exercises=exercises,
        program_pagination={"limit": limit, "offset": program_offset, "total": program_total, "has_more": program_offset + len(programs) < program_total},
        exercise_pagination={"limit": limit, "offset": exercise_offset, "total": exercise_total, "has_more": exercise_offset + len(exercises) < exercise_total})
