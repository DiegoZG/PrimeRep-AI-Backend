"""Create synthetic profile/history fixtures in an explicitly disposable UI database."""

import os
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.engine import make_url

from app.core.database import SessionLocal
from app.core.legal import PRIVACY_VERSION, TERMS_VERSION
from app.core.security.passwords import hash_password
from app.models import Exercise, OnboardingProfile, SetLog, User, WorkoutSession
from app.models.coach import CoachPreference


FIXTURE_ID = "profile-e2e-fixture"
FIXTURE_EMAIL = "primerep-profile-fixture@example.com"
FIXTURE_PASSWORD = "StrongPass123"


def main():
    database = make_url(os.environ["DATABASE_URL"]).database or ""
    if os.environ.get("APP_ENV") != "test" or not database.startswith("primerep_part8_ui_"):
        raise RuntimeError("Requires APP_ENV=test and a primerep_part8_ui_* disposable database")
    with SessionLocal() as db:
        if db.get(User, FIXTURE_ID):
            print("Profile fixture already exists; keeping its current edits and history.")
            return
        exercise = db.get(Exercise, "push_up")
        if exercise is None:
            raise RuntimeError("Canonical push_up exercise is required; apply migrations and canonical seeds first")
        now = datetime.now(timezone.utc)
        local_today = now.astimezone(ZoneInfo("America/New_York")).date()
        monday = local_today - timedelta(days=local_today.weekday())
        user = User(
            id=FIXTURE_ID, email=FIXTURE_EMAIL, preferred_name="Profile", last_name="Fixture",
            password_hash=hash_password(FIXTURE_PASSWORD), has_completed_onboarding=True,
            terms_accepted_version=TERMS_VERSION, privacy_accepted_version=PRIVACY_VERSION,
            legal_accepted_at=now,
        )
        db.add(user)
        db.flush()
        db.add(OnboardingProfile(user_id=user.id, data={
            "_fixture": "profile-part8", "preferredName": user.preferred_name,
            "lastName": user.last_name, "email": user.email,
            "weight": 180, "weightUnit": "LB", "age": 31, "gender": "Male",
            "fitnessGoal": "general-fitness", "experienceLevel": "beginner",
            "workoutFrequency": "3-days", "workoutSplit": "full-body",
            "trainingPlace": "garage-gym", "selectedEquipment": [],
            "dumbbellWeights": [], "plateWeights": [], "varietyLevel": "consistent",
        }))
        db.add(CoachPreference(user_id=user.id, time_zone="America/New_York", notifications_enabled=False))
        dates = [monday - timedelta(weeks=week) + timedelta(days=offset) for week in (4, 3, 2, 1) for offset in (0, 2, 4)]
        for index, workout_date in enumerate(dates, start=1):
            completion = datetime.combine(workout_date, time(16), tzinfo=timezone.utc)
            snapshot = {
                "workoutDayId": f"profile-fixture-day-{index}", "date": workout_date.isoformat(),
                "slotIndex": 0, "durationMinutes": 35, "estimatedMinutes": 35,
                "title": f"Foundation Session {index}", "splitKey": "full-body", "dayType": "full_body",
                "workoutIntent": None, "deferredMuscles": [], "unavailableMuscles": [],
                "exerciseBlocks": [{"blockType": "main", "items": [{
                    "exercise": {
                        "id": exercise.id, "name": exercise.name, "exerciseType": "strength",
                        "primaryMuscle": exercise.primary_muscle,
                        "secondaryMuscles": exercise.secondary_muscles or [], "requiredEquipmentIds": [],
                        "imageUrl": None, "demoVideoUrl": None,
                    },
                    "prescription": {"sets": 3, "repsMin": 8, "repsMax": 12, "restSeconds": 90},
                }]}],
            }
            session = WorkoutSession(
                id=f"profile-fixture-session-{index}", user_id=user.id,
                workout_day_id=snapshot["workoutDayId"], workout_date=workout_date,
                day_type="full_body", workout_snapshot=snapshot,
                client_session_id=f"profile-fixture-client-{index}", status="completed",
                started_at=completion - timedelta(minutes=35), completed_at=completion,
            )
            db.add(session)
            db.flush()
            for set_number in (1, 2, 3):
                db.add(SetLog(
                    id=f"profile-fixture-set-{index}-{set_number}", session_id=session.id,
                    exercise_id=exercise.id, set_number=set_number, reps=10, weight_kg=None,
                    client_operation_id=f"profile-fixture-operation-{index}-{set_number}",
                    logged_at=completion, updated_at=completion,
                ))
        db.commit()
    print("Created synthetic profile account with 12 completed workouts over 4 consecutive active weeks.")


if __name__ == "__main__":
    main()
