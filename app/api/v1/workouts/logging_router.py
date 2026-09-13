"""Workout session logging endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security.deps import get_current_user
from app.core.workout_logging_service import (
    ActiveSessionConflict,
    ExerciseNotInWorkout,
    DuplicateSetSlot,
    InvalidSessionTransition,
    InvalidSessionMutation,
    StaleSetMutation,
    InvalidOperationReuse,
    create_session,
    delete_set,
    get_session,
    get_session_by_client_id,
    get_active_session,
    get_stats,
    list_sessions,
    log_set,
    restore_set,
    transition_session,
    update_exercise_feedback,
    update_set,
    update_workout_note,
)
from app.models.user import User
from app.schemas.workout_logging import (
    SessionCreateRequest,
    SessionListOut,
    SessionOut,
    SessionSummaryOut,
    ExerciseFeedbackRequest,
    ExerciseFeedbackOut,
    MutationOperationRequest,
    SessionNoteRequest,
    SetLogOut,
    SetLogRequest,
    SetLogUpdateRequest,
    StatsOut,
)

router = APIRouter(prefix="/workouts", tags=["workout-logging"])


@router.post("/sessions", response_model=SessionOut, status_code=201)
def create_session_endpoint(
    request: SessionCreateRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Start a new workout session.

    Call this when the user taps "Start Workout". The session is created
    immediately so set logs can be attached to it. Complete it later via
    PATCH /sessions/{id}/complete.
    """
    existing = get_session_by_client_id(db, str(current_user.id), request.client_session_id)
    try:
        session = create_session(
            db,
            user_id=str(current_user.id),
            workout_day_id=request.workout_day_id,
            workout_date=request.workout_date,
            day_type=request.day_type,
            client_session_id=request.client_session_id,
        )
    except ActiveSessionConflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Finish or abandon your active workout before starting another.",
        )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
    return session


@router.get("/sessions/active", response_model=Optional[SessionOut])
def get_active_session_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return get_active_session(db, str(current_user.id))


@router.post("/sessions/{session_id}/sets", response_model=SetLogOut, status_code=201)
def log_set_endpoint(
    session_id: str,
    request: SetLogRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Log a completed set within a session.

    Can be called multiple times as the user completes each set.
    Weight is optional — omit for bodyweight exercises.
    """
    session = get_session(db, session_id, str(current_user.id))
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")

    if session.status == "abandoned":
        raise HTTPException(status_code=409, detail="An abandoned workout is read-only.")

    try:
        logged_set = log_set(
            db,
            session_id=session_id,
            user_id=str(current_user.id),
            exercise_id=request.exercise_id,
            set_number=request.set_number,
            reps=request.reps,
            weight_kg=request.weight_kg,
            client_operation_id=request.client_operation_id,
        )
    except InvalidSessionMutation:
        raise HTTPException(status_code=409, detail="An abandoned workout is read-only.")
    except ExerciseNotInWorkout:
        raise HTTPException(status_code=422, detail="Exercise is not part of this workout.")
    except DuplicateSetSlot:
        raise HTTPException(status_code=409, detail="That set number already exists for this exercise.")
    if logged_set is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return logged_set


@router.patch("/sessions/{session_id}/complete", response_model=SessionOut)
def complete_session_endpoint(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Mark a workout session as complete.

    Sets completed_at to now. Idempotent — completing an already-completed
    session returns the existing record without error.
    """
    try:
        session = transition_session(db, session_id, str(current_user.id), "completed")
    except InvalidSessionTransition:
        raise HTTPException(status_code=409, detail="An abandoned workout cannot be completed.")
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


@router.patch("/sessions/{session_id}/abandon", response_model=SessionOut)
def abandon_session_endpoint(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        session = transition_session(db, session_id, str(current_user.id), "abandoned")
    except InvalidSessionTransition:
        raise HTTPException(status_code=409, detail="A completed workout cannot be abandoned.")
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


@router.get("/sessions", response_model=SessionListOut)
def list_sessions_endpoint(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List the authenticated user's past workout sessions, newest first.

    Each item includes a set_count for summary display.
    """
    items, total = list_sessions(db, user_id=str(current_user.id), limit=limit, offset=offset)

    summaries = [
        SessionSummaryOut(
            id=s.id,
            workoutDayId=s.workout_day_id,
            workoutDate=s.workout_date,
            dayType=s.day_type,
            status=s.status,
            completedAt=s.completed_at,
            setCount=len(s.set_logs),
            title=s.summary["title"],
            totalVolumeKg=s.summary["total_volume_kg"],
        )
        for s in items
    ]

    return SessionListOut(items=summaries, total=total)


@router.get("/sessions/history", response_model=SessionListOut)
def list_completed_sessions_endpoint(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items, total = list_sessions(
        db, user_id=str(current_user.id), limit=limit, offset=offset, completed_only=True
    )
    return SessionListOut(
        items=[
            SessionSummaryOut(
                id=session.id,
                workoutDayId=session.workout_day_id,
                workoutDate=session.workout_date,
                dayType=session.day_type,
                status=session.status,
                completedAt=session.completed_at,
                setCount=len(session.set_logs),
                title=session.summary["title"],
                totalVolumeKg=session.summary["total_volume_kg"],
            )
            for session in items
        ],
        total=total,
    )


def _mutation_error(error: Exception) -> HTTPException:
    if isinstance(error, (InvalidSessionMutation, InvalidOperationReuse, StaleSetMutation)):
        return HTTPException(status_code=409, detail="This workout cannot accept that change.")
    if isinstance(error, ExerciseNotInWorkout):
        return HTTPException(status_code=422, detail="Exercise is not part of this workout.")
    return HTTPException(status_code=400, detail="Could not update workout.")


@router.patch("/sessions/{session_id}/sets/{set_log_id}", response_model=SetLogOut)
def update_set_endpoint(
    session_id: str,
    set_log_id: str,
    request: SetLogUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        set_log = update_set(
            db,
            session_id,
            str(current_user.id),
            set_log_id,
            request.reps,
            request.weight_kg,
            "weightKg" in request.model_fields_set or "weight_kg" in request.model_fields_set,
            request.expected_version,
            request.client_operation_id,
        )
    except Exception as error:
        raise _mutation_error(error)
    if set_log is None:
        raise HTTPException(status_code=404, detail="Set not found.")
    return set_log


@router.delete("/sessions/{session_id}/sets/{set_log_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_set_endpoint(
    session_id: str,
    set_log_id: str,
    client_operation_id: str = Query(..., alias="clientOperationId", min_length=1, max_length=128),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        deleted = delete_set(db, session_id, str(current_user.id), set_log_id, client_operation_id)
    except Exception as error:
        raise _mutation_error(error)
    if not deleted:
        raise HTTPException(status_code=404, detail="Set not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sessions/{session_id}/sets/{set_log_id}/restore", response_model=SetLogOut)
def restore_set_endpoint(
    session_id: str,
    set_log_id: str,
    request: MutationOperationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        set_log = restore_set(db, session_id, str(current_user.id), set_log_id, request.client_operation_id)
    except Exception as error:
        raise _mutation_error(error)
    if set_log is None:
        raise HTTPException(status_code=404, detail="Set not found.")
    return set_log


@router.put("/sessions/{session_id}/note", response_model=SessionOut)
def update_workout_note_endpoint(
    session_id: str,
    request: SessionNoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        session = update_workout_note(db, session_id, str(current_user.id), request.note, request.client_operation_id)
    except Exception as error:
        raise _mutation_error(error)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


@router.put("/sessions/{session_id}/exercises/{exercise_id}/feedback", response_model=ExerciseFeedbackOut)
def update_exercise_feedback_endpoint(
    session_id: str,
    exercise_id: str,
    request: ExerciseFeedbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        feedback = update_exercise_feedback(
            db, session_id, str(current_user.id), exercise_id, request.effort, request.rir, request.client_operation_id
        )
    except Exception as error:
        raise _mutation_error(error)
    if feedback is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return feedback




@router.get("/sessions/stats", response_model=StatsOut)
def get_stats_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return Quick Stats for the authenticated user:
    - streak: consecutive days with a completed workout (ends today or yesterday)
    - totalCompleted: all-time completed session count
    - prsThisWeek: number of exercises where a new weight PR was set this week
    - totalVolumeKg: all-time weight x reps across completed sessions
    """
    stats = get_stats(db, user_id=str(current_user.id))
    return StatsOut(
        streak=stats["streak"],
        totalCompleted=stats["total_completed"],
        prsThisWeek=stats["prs_this_week"],
        totalVolumeKg=stats["total_volume_kg"],
    )


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session_endpoint(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = get_session(db, session_id, str(current_user.id))
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session
