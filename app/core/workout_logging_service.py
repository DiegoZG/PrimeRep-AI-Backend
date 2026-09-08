"""Business logic for workout session logging and stats."""

import copy
import uuid
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.set_log import SetLog
from app.models.workout_session import WorkoutSession
from app.models.workout_week_plan import WorkoutWeekPlan


class ActiveSessionConflict(Exception):
    """Raised when a different in-progress workout already belongs to the user."""


class InvalidSessionTransition(Exception):
    """Raised when a terminal session is asked to transition to another state."""


def _find_workout_snapshot(db: Session, user_id: str, workout_day_id: str) -> Optional[dict]:
    """Return the generated plan entry that owns this opaque workout-day ID."""
    plans = (
        db.query(WorkoutWeekPlan)
        .filter(WorkoutWeekPlan.user_id == user_id)
        .order_by(WorkoutWeekPlan.updated_at.desc())
        .all()
    )
    for plan in plans:
        for workout in plan.plan_json.get("workouts", []):
            if workout.get("workoutDayId") == workout_day_id:
                return copy.deepcopy(workout)
    return None


def _backfill_snapshot(db: Session, session: Optional[WorkoutSession]) -> Optional[WorkoutSession]:
    if session is None or session.workout_snapshot is not None:
        return session
    snapshot = _find_workout_snapshot(db, session.user_id, session.workout_day_id)
    if snapshot is None:
        return session
    session.workout_snapshot = snapshot
    db.commit()
    db.refresh(session)
    return session


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(
    db: Session,
    user_id: str,
    workout_day_id: str,
    workout_date: date,
    day_type: str,
    client_session_id: str,
) -> WorkoutSession:
    existing = get_session_by_client_id(db, user_id, client_session_id)
    if existing is not None:
        return _backfill_snapshot(db, existing)

    # The partial unique index is the final concurrency guard. This lock makes
    # the usual active-session check deterministic when a row already exists.
    active = (
        db.query(WorkoutSession)
        .filter(WorkoutSession.user_id == user_id, WorkoutSession.status == "in_progress")
        .with_for_update()
        .first()
    )
    if active is not None:
        raise ActiveSessionConflict

    snapshot = _find_workout_snapshot(db, user_id, workout_day_id)
    if snapshot is not None:
        workout_date = date.fromisoformat(snapshot["date"])
        day_type = snapshot["dayType"]

    session = WorkoutSession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        workout_day_id=workout_day_id,
        workout_date=workout_date,
        day_type=day_type,
        workout_snapshot=snapshot,
        client_session_id=client_session_id,
    )
    db.add(session)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent start either won using the same idempotency key or
        # created the user's one allowed active session.
        db.rollback()
        existing = get_session_by_client_id(db, user_id, client_session_id)
        if existing is not None:
            return existing
        raise ActiveSessionConflict
    db.refresh(session)
    return session


def get_session_by_client_id(
    db: Session, user_id: str, client_session_id: str
) -> Optional[WorkoutSession]:
    return (
        db.query(WorkoutSession)
        .filter(
            WorkoutSession.user_id == user_id,
            WorkoutSession.client_session_id == client_session_id,
        )
        .first()
    )


def get_session(db: Session, session_id: str, user_id: Optional[str] = None) -> Optional[WorkoutSession]:
    query = db.query(WorkoutSession).filter(WorkoutSession.id == session_id)
    if user_id is not None:
        query = query.filter(WorkoutSession.user_id == user_id)
    return _backfill_snapshot(db, query.first())


def get_active_session(db: Session, user_id: str) -> Optional[WorkoutSession]:
    return _backfill_snapshot(db, (
        db.query(WorkoutSession)
        .filter(WorkoutSession.user_id == user_id, WorkoutSession.status == "in_progress")
        .order_by(WorkoutSession.started_at.desc())
        .first()
    ))


def list_sessions(
    db: Session,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[WorkoutSession], int]:
    base = db.query(WorkoutSession).filter(WorkoutSession.user_id == user_id)
    total = base.count()
    items = (
        base.order_by(WorkoutSession.workout_date.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return items, total


def transition_session(db: Session, session_id: str, user_id: str, target_status: str) -> WorkoutSession:
    session = (
        db.query(WorkoutSession)
        .filter(WorkoutSession.id == session_id, WorkoutSession.user_id == user_id)
        .with_for_update()
        .first()
    )
    if session is None:
        return None
    if session.status == target_status:
        return session
    if session.status != "in_progress":
        raise InvalidSessionTransition
    session.status = target_status
    if target_status == "completed":
        session.completed_at = func.now()
    db.commit()
    db.refresh(session)
    return session


# ── Set Logs ──────────────────────────────────────────────────────────────────

def log_set(
    db: Session,
    session_id: str,
    user_id: str,
    exercise_id: str,
    set_number: int,
    reps: int,
    weight_kg: Optional[float],
    client_operation_id: str,
) -> SetLog:
    session = (
        db.query(WorkoutSession)
        .filter(WorkoutSession.id == session_id, WorkoutSession.user_id == user_id)
        .with_for_update()
        .first()
    )
    if session is None:
        return None
    if session.status != "in_progress":
        raise InvalidSessionTransition
    existing = (
        db.query(SetLog)
        .filter(
            SetLog.session_id == session_id,
            SetLog.client_operation_id == client_operation_id,
        )
        .first()
    )
    if existing is not None:
        return existing
    set_log = SetLog(
        id=str(uuid.uuid4()),
        session_id=session_id,
        exercise_id=exercise_id,
        set_number=set_number,
        reps=reps,
        weight_kg=weight_kg,
        client_operation_id=client_operation_id,
    )
    db.add(set_log)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(SetLog)
            .filter(
                SetLog.session_id == session_id,
                SetLog.client_operation_id == client_operation_id,
            )
            .first()
        )
        if existing is not None:
            return existing
        raise
    db.refresh(set_log)
    return set_log


def get_last_sets(
    db: Session,
    user_id: str,
    exercise_id: str,
    limit: int = 3,
) -> list[SetLog]:
    """Most recent logged sets for an exercise, from the user's completed sessions."""
    return (
        db.query(SetLog)
        .join(WorkoutSession, SetLog.session_id == WorkoutSession.id)
        .filter(
            WorkoutSession.user_id == user_id,
            WorkoutSession.completed_at.isnot(None),
            SetLog.exercise_id == exercise_id,
        )
        .order_by(SetLog.logged_at.desc(), SetLog.id.desc())
        .limit(limit)
        .all()
    )


def get_recent_completed_sets_by_session(
    db: Session,
    user_id: str,
    exercise_id: str,
    session_limit: int = 2,
) -> list[tuple[WorkoutSession, list[SetLog]]]:
    """Completed sessions that include `exercise_id`, newest first, with that exercise's sets.

    Same join/filter as `get_last_sets` — grouped by session so progression can
    judge a whole outing rather than a handful of individual rows.
    """
    session_ids = [
        row[0]
        for row in (
            db.query(WorkoutSession.id)
            .join(SetLog, SetLog.session_id == WorkoutSession.id)
            .filter(
                WorkoutSession.user_id == user_id,
                WorkoutSession.completed_at.isnot(None),
                SetLog.exercise_id == exercise_id,
            )
            .group_by(WorkoutSession.id)
            .order_by(
                func.max(WorkoutSession.workout_date).desc(),
                func.max(WorkoutSession.completed_at).desc(),
                WorkoutSession.id.desc(),
            )
            .limit(session_limit)
            .all()
        )
    ]
    if not session_ids:
        return []

    sessions = {
        session.id: session
        for session in db.query(WorkoutSession).filter(WorkoutSession.id.in_(session_ids)).all()
    }
    sets = (
        db.query(SetLog)
        .filter(SetLog.session_id.in_(session_ids), SetLog.exercise_id == exercise_id)
        .order_by(SetLog.set_number.asc(), SetLog.logged_at.asc())
        .all()
    )
    sets_by_session: dict[str, list[SetLog]] = {sid: [] for sid in session_ids}
    for set_log in sets:
        sets_by_session[set_log.session_id].append(set_log)

    return [(sessions[sid], sets_by_session[sid]) for sid in session_ids if sid in sessions]


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats(db: Session, user_id: str) -> dict:
    return {
        "streak": _calculate_streak(db, user_id),
        "total_completed": _count_completed(db, user_id),
        "prs_this_week": _count_prs_this_week(db, user_id),
        "total_volume_kg": _total_volume_kg(db, user_id),
    }


def _calculate_streak(db: Session, user_id: str) -> int:
    """Count consecutive days with a completed session ending today or yesterday."""
    completed_dates = (
        db.query(WorkoutSession.workout_date)
        .filter(
            WorkoutSession.user_id == user_id,
            WorkoutSession.completed_at.isnot(None),
        )
        .distinct()
        .order_by(WorkoutSession.workout_date.desc())
        .all()
    )

    if not completed_dates:
        return 0

    today = date.today()
    yesterday = today - timedelta(days=1)
    date_list = [row[0] for row in completed_dates]

    # Streak requires the most recent workout to be today or yesterday
    if date_list[0] not in (today, yesterday):
        return 0

    streak = 1
    for i in range(1, len(date_list)):
        if (date_list[i - 1] - date_list[i]) == timedelta(days=1):
            streak += 1
        else:
            break

    return streak


def _count_completed(db: Session, user_id: str) -> int:
    return (
        db.query(WorkoutSession)
        .filter(
            WorkoutSession.user_id == user_id,
            WorkoutSession.completed_at.isnot(None),
        )
        .count()
    )


def _total_volume_kg(db: Session, user_id: str) -> float:
    """All-time volume load across completed sessions: sum of weight x reps.

    Sets logged without a weight are excluded rather than counted as zero-weight
    reps. Bodyweight movements would need a per-session bodyweight figure the app
    does not track, and guessing one would quietly inflate the number.
    """
    total = (
        db.query(func.sum(SetLog.weight_kg * SetLog.reps))
        .join(WorkoutSession, SetLog.session_id == WorkoutSession.id)
        .filter(
            WorkoutSession.user_id == user_id,
            WorkoutSession.completed_at.isnot(None),
            SetLog.weight_kg.isnot(None),
        )
        .scalar()
    )

    return round(float(total or 0.0), 2)


def _count_prs_this_week(db: Session, user_id: str) -> int:
    """Count exercises where the user set a new personal weight record this week."""
    week_start = date.today() - timedelta(days=date.today().weekday())

    # Max weight per exercise in completed sessions this week
    this_week = (
        db.query(SetLog.exercise_id, func.max(SetLog.weight_kg).label("max_weight"))
        .join(WorkoutSession, SetLog.session_id == WorkoutSession.id)
        .filter(
            WorkoutSession.user_id == user_id,
            WorkoutSession.completed_at.isnot(None),
            WorkoutSession.workout_date >= week_start,
            SetLog.weight_kg.isnot(None),
        )
        .group_by(SetLog.exercise_id)
        .all()
    )

    if not this_week:
        return 0

    pr_count = 0
    for exercise_id, max_this_week in this_week:
        prior_max = (
            db.query(func.max(SetLog.weight_kg))
            .join(WorkoutSession, SetLog.session_id == WorkoutSession.id)
            .filter(
                WorkoutSession.user_id == user_id,
                WorkoutSession.completed_at.isnot(None),
                WorkoutSession.workout_date < week_start,
                SetLog.exercise_id == exercise_id,
                SetLog.weight_kg.isnot(None),
            )
            .scalar()
        )

        if prior_max is None or max_this_week > prior_max:
            pr_count += 1

    return pr_count
