"""Create or remove the deterministic local Coach Maestro fixture."""

from __future__ import annotations

import argparse
import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.coach_feed_service import _render_ai_variant, reconcile_feed
from app.core.database import SessionLocal
from app.core.legal import PRIVACY_VERSION, TERMS_VERSION
from app.core.security.passwords import hash_password
from app.core.workout_week_service import ONBOARDING_WORKOUT_CONTRACT_VERSION
from app.models.coach import CoachFeedItem, CoachPreference
from app.models.exercise import Exercise
from app.models.onboarding_profile import OnboardingProfile
from app.models.set_log import SetLog
from app.models.user import User
from app.models.workout_session import WorkoutSession
from app.models.workout_session_exercise_feedback import WorkoutSessionExerciseFeedback
from app.models.workout_template import UserProgramActivation, WorkoutTemplate
from app.models.workout_week_plan import WorkoutWeekPlan


FIXTURE_EMAIL = "coach-fixture@example.com"
FIXTURE_PASSWORD = "StrongPass123"
FIXTURE_USER_ID = "coach-e2e-fixture-user"
FIXTURE_TIME_ZONE = "America/New_York"
FIXTURE_WORKOUT_ID = "coach-fixture-planned-workout"
PROGRESSION_ITEM_ID = "coach-fixture-progression"
CONSISTENCY_ITEM_ID = "coach-fixture-consistency"
OLDER_ITEM_ID = "coach-fixture-older"
ALLOWED_ENVIRONMENTS = {"local", "dev", "development", "test"}
FIXTURE_MARKER = {"id": "coach-e2e-maestro", "version": 1}


def assert_local_environment() -> None:
    app_env = os.environ.get("APP_ENV")
    if app_env not in ALLOWED_ENVIRONMENTS:
        raise RuntimeError("Coach E2E fixtures are restricted to local/test environments.")


def _fixture_user(db: Session) -> Optional[User]:
    id_user = db.query(User).filter(User.id == FIXTURE_USER_ID).one_or_none()
    email_user = (
        db.query(User)
        .filter(func.lower(User.email) == FIXTURE_EMAIL.lower())
        .one_or_none()
    )
    if id_user is None and email_user is None:
        return None
    if id_user is None or email_user is None or id_user.id != email_user.id:
        raise RuntimeError("Coach E2E fixture identity collides with an existing account.")
    profile = db.get(OnboardingProfile, id_user.id)
    if profile is None or profile.data.get("_fixture") != FIXTURE_MARKER:
        raise RuntimeError("Coach E2E fixture identity is not owned by this fixture.")
    return id_user


def teardown_fixture(db: Session) -> bool:
    assert_local_environment()
    user = _fixture_user(db)
    if user is None:
        return False
    db.delete(user)
    db.commit()
    return True


def _exercise_payload(exercise: Exercise) -> dict:
    return {
        "id": exercise.id,
        "name": exercise.name,
        "exerciseType": exercise.exercise_type,
        "primaryMuscle": exercise.primary_muscle,
        "secondaryMuscles": list(exercise.secondary_muscles or []),
        "imageUrl": exercise.image_url,
        "demoVideoUrl": exercise.demo_video_url,
        "requiredEquipmentIds": [equipment.id for equipment in exercise.equipment],
    }


def _workout(
    *, workout_id: str, workout_date: date, title: str, exercises: list[Exercise]
) -> dict:
    return {
        "workoutDayId": workout_id,
        "date": workout_date.isoformat(),
        "durationMinutes": 45,
        "title": title,
        "splitKey": "full-body",
        "dayType": "full_body",
        "estimatedMinutes": 45,
        "workoutIntent": "Build strength with controlled, repeatable sets.",
        "deferredMuscles": [],
        "unavailableMuscles": [],
        "exerciseBlocks": [
            {
                "blockType": "main",
                "items": [
                    {
                        "exercise": _exercise_payload(exercise),
                        "prescription": {
                            "sets": 3,
                            "repsMin": 8,
                            "repsMax": 10,
                            "restSeconds": 90,
                            "suggestedWeightKg": None,
                            "suggestedWeightReason": None,
                        },
                        "exerciseRationale": None,
                    }
                    for exercise in exercises
                ],
            }
        ],
    }


def _plan_json(week_start: date, workouts: list[dict], now: datetime) -> dict:
    ordered_workouts = [
        {**workout, "slotIndex": slot_index}
        for slot_index, workout in enumerate(
            sorted(workouts, key=lambda workout: workout["date"])
        )
    ]
    return {
        "weekStart": week_start.isoformat(),
        "daysPerWeek": len(workouts),
        "onboardingWorkoutContractVersion": ONBOARDING_WORKOUT_CONTRACT_VERSION,
        "generatedAt": now.isoformat(),
        "seed": "coach-e2e-fixture",
        "workouts": ordered_workouts,
        "programActivationId": None,
        "templateId": None,
        "templateVersion": None,
    }


def seed_fixture(db: Session, *, local_date: Optional[date] = None) -> str:
    assert_local_environment()
    local_date = local_date or datetime.now(ZoneInfo(FIXTURE_TIME_ZONE)).date()
    now = datetime.now(timezone.utc)
    exercises = (
        db.query(Exercise)
        .filter(Exercise.is_active.is_(True), Exercise.source == "seed")
        .order_by(Exercise.sort_order.asc(), Exercise.id.asc())
        .limit(15)
        .all()
    )
    template = (
        db.query(WorkoutTemplate)
        .filter(
            WorkoutTemplate.scope == "system",
            WorkoutTemplate.is_archived.is_(False),
        )
        .order_by(WorkoutTemplate.featured_rank.asc().nullslast(), WorkoutTemplate.id.asc())
        .first()
    )
    if len(exercises) != 15 or template is None:
        raise RuntimeError(
            "Seed exercises and system programs are required; run migrations and canonical seeds first."
        )

    existing_user = _fixture_user(db)
    if existing_user is not None:
        db.delete(existing_user)
        db.flush()

    user = User(
        id=FIXTURE_USER_ID,
        email=FIXTURE_EMAIL,
        preferred_name="Coach",
        last_name="Fixture",
        password_hash=hash_password(FIXTURE_PASSWORD),
        has_completed_onboarding=True,
        subscription_tier="premium",
        coach_insights_enabled=True,
        terms_accepted_version=TERMS_VERSION,
        privacy_accepted_version=PRIVACY_VERSION,
        legal_accepted_at=now,
    )
    db.add(user)
    db.flush()
    db.add(
        OnboardingProfile(
            user_id=user.id,
            data={
                "_fixture": FIXTURE_MARKER,
                "workoutFrequency": "3-days",
                "workoutSplit": "full-body",
                "selectedEquipment": [],
            },
        )
    )
    db.add(
        CoachPreference(
            user_id=user.id,
            notifications_enabled=True,
            reminder_time=time(8, 0),
            time_zone=FIXTURE_TIME_ZONE,
        )
    )

    missed_date = local_date - timedelta(days=1)
    current_week_start = local_date - timedelta(days=local_date.weekday())
    planned_workout = _workout(
        workout_id=FIXTURE_WORKOUT_ID,
        workout_date=local_date,
        title="Coach Fixture Full Body",
        exercises=exercises,
    )
    missed_workout = _workout(
        workout_id="coach-fixture-missed-workout",
        workout_date=missed_date,
        title="Coach Fixture Missed Session",
        exercises=exercises[:3],
    )
    current_workouts = [planned_workout]
    if missed_date >= current_week_start:
        current_workouts.append(missed_workout)
    used_dates = {date.fromisoformat(workout["date"]) for workout in current_workouts}
    available_dates = [
        current_week_start + timedelta(days=offset)
        for offset in range(7)
        if current_week_start + timedelta(days=offset) not in used_dates
    ]
    available_dates.sort(key=lambda candidate: (candidate < local_date, candidate))
    filler_workouts = [
        _workout(
            workout_id=f"coach-fixture-scheduled-{index + 1}",
            workout_date=workout_date,
            title=f"Coach Fixture Scheduled Session {index + 1}",
            exercises=exercises[:3],
        )
        for index, workout_date in enumerate(
            available_dates[: 3 - len(current_workouts)]
        )
    ]
    current_workouts.extend(filler_workouts)

    by_week: dict[date, list[dict]] = {}
    all_planned_workouts = list(current_workouts)
    if missed_date < current_week_start:
        all_planned_workouts.append(missed_workout)
    for workout in all_planned_workouts:
        workout_date = date.fromisoformat(workout["date"])
        week_start = workout_date - timedelta(days=workout_date.weekday())
        by_week.setdefault(week_start, []).append(workout)
    for index, (week_start, workouts) in enumerate(sorted(by_week.items())):
        db.add(
            WorkoutWeekPlan(
                id=f"coach-fixture-week-{index}",
                user_id=user.id,
                week_start_date=week_start,
                days_per_week=len(workouts),
                plan_json=_plan_json(week_start, workouts, now),
            )
        )

    sessions: list[WorkoutSession] = []
    completed_filler_ids = [
        workout["workoutDayId"]
        for workout in filler_workouts
        if date.fromisoformat(workout["date"]) < local_date
    ]
    for index in range(10):
        completed_at = now - timedelta(days=9 - index, hours=1)
        session = WorkoutSession(
            id=f"coach-fixture-session-{index + 1}",
            user_id=user.id,
            workout_day_id=(
                completed_filler_ids[index]
                if index < len(completed_filler_ids)
                else f"coach-fixture-completed-{index + 1}"
            ),
            workout_date=completed_at.date(),
            day_type="full_body",
            workout_snapshot={
                "title": f"Fixture completed workout {index + 1}",
                "exerciseBlocks": [],
            },
            client_session_id=f"coach-fixture-client-session-{index + 1}",
            status="completed",
            started_at=completed_at - timedelta(minutes=45),
            completed_at=completed_at,
        )
        db.add(session)
        sessions.append(session)

    baseline = sessions[-2]
    latest = sessions[-1]
    db.add(
        SetLog(
            id="coach-fixture-baseline-set",
            session_id=baseline.id,
            exercise_id=exercises[0].id,
            set_number=1,
            reps=10,
            weight_kg=20,
            client_operation_id="coach-fixture-baseline-operation",
            version=1,
            logged_at=baseline.completed_at - timedelta(minutes=5),
        )
    )
    for index, exercise in enumerate(exercises):
        db.add(
            SetLog(
                id=f"coach-fixture-latest-set-{index + 1}",
                session_id=latest.id,
                exercise_id=exercise.id,
                set_number=1,
                reps=10,
                weight_kg=25 if index == 0 else 20,
                client_operation_id=f"coach-fixture-latest-operation-{index + 1}",
                version=1,
                logged_at=latest.completed_at - timedelta(minutes=15 - index),
            )
        )
    for index, exercise in enumerate(exercises[:2]):
        db.add(
            WorkoutSessionExerciseFeedback(
                id=f"coach-fixture-feedback-{index + 1}",
                session_id=latest.id,
                exercise_id=exercise.id,
                effort="hard",
                rir=0,
            )
        )

    changed_at = now.isoformat()
    db.add(
        UserProgramActivation(
            id="coach-fixture-activation",
            user_id=user.id,
            template_id=template.id,
            template_version=template.version,
            status="scheduled",
            effective_date=local_date + timedelta(days=7),
            weekdays=list(range(template.frequency)),
            activation_snapshot={
                "name": template.name,
                "days": [],
                "equipmentReviewFingerprint": "coach-fixture-equipment-review",
                "equipmentReviewChangedAt": changed_at,
                "equipmentReviewRevision": 1,
            },
            apply_mode="now",
            client_operation_id="coach-fixture-activation-operation",
            request_fingerprint="coach-fixture-activation-request",
            requires_review=True,
        )
    )
    db.commit()

    reconcile_feed(db, user.id, local_date)
    visible = (
        db.query(CoachFeedItem)
        .filter(
            CoachFeedItem.user_id == user.id,
            CoachFeedItem.kind != "upcoming_workout",
            CoachFeedItem.expires_at > now,
        )
        .all()
    )
    progression = next(
        item
        for item in visible
        if item.kind == "progression"
        and item.target_data.get("workoutDayId") == FIXTURE_WORKOUT_ID
    )
    main_consistency = next(
        item
        for item in visible
        if item.kind == "consistency"
        and item.protected_facts.get("completedWorkoutMilestone") == 10
    )
    older_consistency = next(
        item
        for item in visible
        if item.kind == "consistency"
        and item.protected_facts.get("completedWorkoutMilestone") == 5
    )
    db.query(CoachFeedItem).filter(CoachFeedItem.id == progression.id).update(
        {"id": PROGRESSION_ITEM_ID}, synchronize_session=False
    )
    db.query(CoachFeedItem).filter(CoachFeedItem.id == main_consistency.id).update(
        {
            "id": CONSISTENCY_ITEM_ID,
            "created_at": now,
        },
        synchronize_session=False,
    )
    db.query(CoachFeedItem).filter(CoachFeedItem.id == older_consistency.id).update(
        {
            "id": OLDER_ITEM_ID,
            "created_at": now - timedelta(days=1),
        },
        synchronize_session=False,
    )
    db.flush()
    progression = db.get(CoachFeedItem, PROGRESSION_ITEM_ID)
    progression.ai_title, progression.ai_body, progression.ai_detail = _render_ai_variant(
        progression, "focused"
    )
    progression.ai_status = "enriched"
    progression.ai_attempt_count = 1
    db.commit()

    return user.id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--teardown",
        action="store_true",
        help="Remove only the deterministic Coach fixture account and its cascaded data.",
    )
    args = parser.parse_args()
    assert_local_environment()
    with SessionLocal() as db:
        if args.teardown:
            removed = teardown_fixture(db)
            print("Coach E2E fixture removed." if removed else "Coach E2E fixture was not present.")
            return
        seed_fixture(db)
    print(f"Coach E2E fixture ready for {FIXTURE_EMAIL}.")


if __name__ == "__main__":
    main()
