from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.coach import CoachPreference
from app.models.workout_session import WorkoutSession


CONSISTENCY_THRESHOLDS = (2, 4, 8, 12)


def workout_milestones(total: int, *, include_next: bool = False) -> list[int]:
    thresholds = [1, 5, 10, 25, 50, 100]
    thresholds.extend(range(150, total + 51, 50))
    earned = [value for value in thresholds if value <= total]
    if include_next:
        earned.append(next(value for value in thresholds if value > total))
    return earned


def monday(value: date) -> date:
    return value - timedelta(days=value.weekday())


def calculate_achievements(completions, *, time_zone: str, now: datetime) -> dict:
    zone = ZoneInfo(time_zone)
    current_week = monday(now.astimezone(zone).date())
    counts = Counter()
    week_first = {}
    total = 0
    milestone_dates = {}
    for completed_at in completions:
        total += 1
        if total in (1, 5, 10, 25) or total % 50 == 0:
            milestone_dates[total] = completed_at
        week = monday(completed_at.astimezone(zone).date())
        counts[week] += 1
        week_first.setdefault(week, completed_at)

    best = running = 0
    previous = None
    consistency_dates = {}
    for week in sorted(counts):
        running = running + 1 if previous == week - timedelta(weeks=1) else 1
        best = max(best, running)
        if running in CONSISTENCY_THRESHOLDS:
            consistency_dates.setdefault(running, week_first[week])
        previous = week

    cursor = current_week if counts[current_week] else current_week - timedelta(weeks=1)
    current = 0
    while counts[cursor]:
        current += 1
        cursor -= timedelta(weeks=1)

    items = [
        {
            "id": f"workouts-{threshold}",
            "title": f"{threshold} {'workout' if threshold == 1 else 'workouts'}",
            "description": f"Complete {threshold} {'workout' if threshold == 1 else 'workouts'}.",
            "kind": "workouts",
            "threshold": threshold,
            "progress": min(total, threshold),
            "earned_at": milestone_dates.get(threshold),
        }
        for threshold in workout_milestones(total, include_next=True)
    ]
    items.extend(
        {
            "id": f"active-weeks-{threshold}",
            "title": f"{threshold} active weeks",
            "description": f"Complete a workout in {threshold} consecutive Monday–Sunday weeks.",
            "kind": "consistency",
            "threshold": threshold,
            "progress": min(best if threshold in consistency_dates else current, threshold),
            "earned_at": consistency_dates.get(threshold),
        }
        for threshold in CONSISTENCY_THRESHOLDS
    )
    return {
        "total_completed": total,
        "current_active_weeks": current,
        "best_active_weeks": best,
        "time_zone": time_zone,
        "weekly_activity": [
            {
                "week_start": current_week - timedelta(weeks=offset),
                "completed_workouts": counts[current_week - timedelta(weeks=offset)],
                "is_current": offset == 0,
            }
            for offset in reversed(range(12))
        ],
        "items": items,
    }


def get_achievements(db: Session, user_id: str, requested_zone: str | None = None) -> dict:
    if requested_zone is not None:
        try:
            ZoneInfo(requested_zone)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("Choose a valid timezone.") from error
        db.execute(
            insert(CoachPreference)
            .values(user_id=user_id, time_zone=requested_zone)
            .on_conflict_do_nothing(index_elements=[CoachPreference.user_id])
        )
        db.commit()
    preference = db.get(CoachPreference, user_id)
    time_zone = preference.time_zone if preference else "UTC"
    try:
        ZoneInfo(time_zone)
    except (ValueError, ZoneInfoNotFoundError):
        time_zone = "UTC"
    completions = (
        db.query(WorkoutSession.completed_at)
        .filter(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == "completed",
            WorkoutSession.completed_at.isnot(None),
        )
        .order_by(WorkoutSession.completed_at, WorkoutSession.id)
        .yield_per(250)
    )
    return calculate_achievements(
        (row.completed_at for row in completions),
        time_zone=time_zone,
        now=datetime.now(timezone.utc),
    )
