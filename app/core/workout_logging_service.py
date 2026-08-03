"""Business logic for workout session logging and stats."""

import uuid
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.set_log import SetLog
from app.models.workout_session import WorkoutSession


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(
    db: Session,
    user_id: str,
    workout_day_id: str,
    workout_date: date,
    day_type: str,
) -> WorkoutSession:
    session = WorkoutSession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        workout_day_id=workout_day_id,
        workout_date=workout_date,
        day_type=day_type,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_session(db: Session, session_id: str) -> Optional[WorkoutSession]:
    return db.query(WorkoutSession).filter(WorkoutSession.id == session_id).first()


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


def complete_session(db: Session, session: WorkoutSession) -> WorkoutSession:
    session.completed_at = func.now()
    db.commit()
    db.refresh(session)
    return session


# ── Set Logs ──────────────────────────────────────────────────────────────────

def log_set(
    db: Session,
    session_id: str,
    exercise_id: str,
    set_number: int,
    reps: int,
    weight_kg: Optional[float],
) -> SetLog:
    set_log = SetLog(
        id=str(uuid.uuid4()),
        session_id=session_id,
        exercise_id=exercise_id,
        set_number=set_number,
        reps=reps,
        weight_kg=weight_kg,
    )
    db.add(set_log)
    db.commit()
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


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats(db: Session, user_id: str) -> dict:
    return {
        "streak": _calculate_streak(db, user_id),
        "total_completed": _count_completed(db, user_id),
        "prs_this_week": _count_prs_this_week(db, user_id),
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
