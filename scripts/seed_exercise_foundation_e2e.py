"""Seed synthetic UI fixtures only into an explicitly disposable test database."""

import argparse
import os

from sqlalchemy.engine import make_url

from app.core.database import SessionLocal
from app.core.security.passwords import hash_password
from app.models import Exercise, OnboardingProfile, User


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-url", required=True, help="URL of a synthetic test clip, not exercise instruction")
    args = parser.parse_args()
    database = make_url(os.environ["DATABASE_URL"]).database or ""
    if os.environ.get("APP_ENV") != "test" or not database.startswith("primerep_part75_ui_"):
        raise RuntimeError("Requires APP_ENV=test and a primerep_part75_ui_* disposable database")
    with SessionLocal() as db:
        user = db.get(User, "exercise-foundation-e2e")
        if not user:
            user = User(id="exercise-foundation-e2e", email="exercise-foundation@example.com", preferred_name="Foundation Fixture", password_hash=hash_password("StrongPass123"), has_completed_onboarding=True)
            db.add(user)
            db.flush()
            db.add(OnboardingProfile(user_id=user.id, data={"_fixture": "exercise-foundation", "trainingFrequency": 3, "fitnessGoal": "general-fitness", "fitnessExperience": "beginner", "equipmentIds": []}))
        user.email = "exercise-foundation@example.com"
        for number in range(1, 251):
            exercise_id = f"foundation_fixture_{number:03d}"
            existing = db.get(Exercise, exercise_id)
            if existing:
                if number == 201:
                    existing.published_media = {"id": "synthetic-test-clip", "url": args.video_url, "mime_type": "video/mp4", "poster_url": None}
                continue
            db.add(Exercise(
                id=exercise_id, name=f"Foundation Fixture {number:03d}",
                exercise_type="strength", primary_muscle="chest", secondary_muscles=[],
                source="seed", publication_status="legacy", generation_eligible=False,
                how_to="Synthetic UI fixture. Not exercise instruction.",
                published_media={"id": "synthetic-test-clip", "url": args.video_url, "mime_type": "video/mp4", "poster_url": None} if number == 201 else None,
            ))
        db.commit()
    print("Created disposable account and 250 synthetic exercise fixtures. No review approvals recorded.")


if __name__ == "__main__":
    main()
