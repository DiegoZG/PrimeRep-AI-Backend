import datetime
import uuid

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.exercise import Exercise, user_exercise_favorites
from app.models.exercise_question import ExerciseQuestion
from app.models.onboarding_profile import OnboardingProfile
from app.models.set_log import SetLog
from app.models.user import User, UserDailyForceRegen
from app.models.user_equipment_weights import UserEquipmentWeights
from app.models.workout_day_history import WorkoutDayHistory
from app.models.workout_session import WorkoutSession
from app.models.workout_week_plan import WorkoutWeekPlan


client = TestClient(app)


def _signup(prefix: str, onboarding=None):
    response = client.post(
        "/v1/auth/signup",
        json={
            "email": f"delete_{prefix}_{uuid.uuid4().hex[:8]}@example.com",
            "password": "StrongPass123",
            "preferred_name": "Delete",
            "last_name": "Test",
            "onboarding": onboarding,
        },
    )
    assert response.status_code == 201
    return response.json()


def _auth(token: str):
    return {"Authorization": f"Bearer {token}"}


def test_delete_account_requires_authentication():
    response = client.delete("/v1/users/me")

    assert response.status_code == 401


def test_delete_account_returns_empty_204_and_invalidates_tokens():
    tokens = _signup("tokens")

    response = client.delete(
        "/v1/users/me", headers=_auth(tokens["access_token"])
    )

    assert response.status_code == 204
    assert response.content == b""
    assert client.get(
        "/v1/users/me", headers=_auth(tokens["access_token"])
    ).status_code == 401
    assert client.post(
        "/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    ).status_code == 401


def test_delete_account_cascades_dependents_and_anonymizes_owned_exercises():
    tokens = _signup(
        "cascade",
        onboarding={"dumbbellWeights": [10, 20], "plateWeights": [5, 10]},
    )
    other_tokens = _signup(
        "other",
        onboarding={"dumbbellWeights": [25], "plateWeights": [45]},
    )
    deleted_user_id = client.get(
        "/v1/users/me", headers=_auth(tokens["access_token"])
    ).json()["id"]
    other_user_id = client.get(
        "/v1/users/me", headers=_auth(other_tokens["access_token"])
    ).json()["id"]
    today = datetime.date.today()
    owned_exercise_id = f"owned-{uuid.uuid4().hex}"
    session_id = str(uuid.uuid4())

    with SessionLocal() as db:
        owned_exercise = Exercise(
            id=owned_exercise_id,
            name="Owned Exercise",
            exercise_type="strength",
            primary_muscle="chest",
            secondary_muscles=[],
            source="user",
            owner_user_id=deleted_user_id,
        )
        session = WorkoutSession(
            id=session_id,
            user_id=deleted_user_id,
            workout_day_id="delete-day",
            workout_date=today,
            day_type="upper",
        )
        db.add_all(
            [
                owned_exercise,
                session,
                WorkoutWeekPlan(
                    user_id=deleted_user_id,
                    week_start_date=today - datetime.timedelta(days=today.weekday()),
                    days_per_week=3,
                    plan_json={"days": []},
                ),
                WorkoutDayHistory(
                    user_id=deleted_user_id,
                    workout_date=today,
                    day_type="upper",
                ),
                ExerciseQuestion(
                    user_id=deleted_user_id,
                    exercise_id="push_up",
                    question="How?",
                    answer="Carefully.",
                ),
                UserDailyForceRegen(
                    user_id=deleted_user_id,
                    regen_date=today,
                    count=1,
                ),
            ]
        )
        db.flush()
        db.add(
            SetLog(
                session_id=session_id,
                exercise_id="push_up",
                set_number=1,
                reps=10,
            )
        )
        db.execute(
            user_exercise_favorites.insert().values(
                user_id=deleted_user_id,
                exercise_id="push_up",
            )
        )
        db.commit()

    response = client.delete(
        "/v1/users/me", headers=_auth(tokens["access_token"])
    )
    assert response.status_code == 204

    with SessionLocal() as db:
        assert db.get(User, deleted_user_id) is None
        assert db.get(OnboardingProfile, deleted_user_id) is None
        assert db.get(UserEquipmentWeights, deleted_user_id) is None
        assert db.query(WorkoutWeekPlan).filter_by(user_id=deleted_user_id).count() == 0
        assert db.query(WorkoutDayHistory).filter_by(user_id=deleted_user_id).count() == 0
        assert db.query(WorkoutSession).filter_by(user_id=deleted_user_id).count() == 0
        assert db.query(SetLog).filter_by(session_id=session_id).count() == 0
        assert db.query(ExerciseQuestion).filter_by(user_id=deleted_user_id).count() == 0
        assert db.query(UserDailyForceRegen).filter_by(user_id=deleted_user_id).count() == 0
        assert db.execute(
            user_exercise_favorites.select().where(
                user_exercise_favorites.c.user_id == deleted_user_id
            )
        ).first() is None
        owned_exercise = db.get(Exercise, owned_exercise_id)
        assert owned_exercise is not None
        assert owned_exercise.owner_user_id is None
        assert db.get(User, other_user_id) is not None
        assert db.get(OnboardingProfile, other_user_id) is not None
        assert db.get(UserEquipmentWeights, other_user_id) is not None
        db.delete(owned_exercise)
        db.commit()

    assert client.get(
        "/v1/users/me", headers=_auth(other_tokens["access_token"])
    ).status_code == 200
    assert client.delete(
        "/v1/users/me", headers=_auth(other_tokens["access_token"])
    ).status_code == 204
