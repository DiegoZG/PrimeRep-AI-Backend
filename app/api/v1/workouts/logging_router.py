"""Workout session logging endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security.deps import get_current_user
from app.core.workout_logging_service import (
    complete_session,
    create_session,
    get_session,
    get_stats,
    list_sessions,
    log_set,
)
from app.models.user import User
from app.schemas.workout_logging import (
    SessionCreateRequest,
    SessionListOut,
    SessionOut,
    SessionSummaryOut,
    SetLogOut,
    SetLogRequest,
    StatsOut,
)

router = APIRouter(prefix="/workouts", tags=["workout-logging"])


@router.post("/sessions", response_model=SessionOut, status_code=201)
def create_session_endpoint(
    request: SessionCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Start a new workout session.

    Call this when the user taps "Start Workout". The session is created
    immediately so set logs can be attached to it. Complete it later via
    PATCH /sessions/{id}/complete.
    """
    session = create_session(
        db,
        user_id=str(current_user.id),
        workout_day_id=request.workout_day_id,
        workout_date=request.workout_date,
        day_type=request.day_type,
    )
    return session


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
    session = get_session(db, session_id)
    if session is None or session.user_id != str(current_user.id):
        raise HTTPException(status_code=404, detail="Session not found.")

    if session.completed_at is not None:
        raise HTTPException(status_code=409, detail="Session is already completed.")

    return log_set(
        db,
        session_id=session_id,
        exercise_id=request.exercise_id,
        set_number=request.set_number,
        reps=request.reps,
        weight_kg=request.weight_kg,
    )


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
    session = get_session(db, session_id)
    if session is None or session.user_id != str(current_user.id):
        raise HTTPException(status_code=404, detail="Session not found.")

    if session.completed_at is not None:
        return session

    return complete_session(db, session)


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
            completedAt=s.completed_at,
            setCount=len(s.set_logs),
        )
        for s in items
    ]

    return SessionListOut(items=summaries, total=total)


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
    """
    stats = get_stats(db, user_id=str(current_user.id))
    return StatsOut(
        streak=stats["streak"],
        totalCompleted=stats["total_completed"],
        prsThisWeek=stats["prs_this_week"],
    )
