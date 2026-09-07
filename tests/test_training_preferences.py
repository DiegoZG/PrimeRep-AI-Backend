import uuid
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.user import User
from app.models.workout_week_plan import WorkoutWeekPlan


client = TestClient(app)


def _scheduled_dates(week_start: str, offsets: tuple[int, ...]) -> list[str]:
    start = date.fromisoformat(week_start)
    return [(start + timedelta(days=offset)).isoformat() for offset in offsets]


def _signup() -> dict[str, str]:
    email = f"preferences_{uuid.uuid4().hex}@example.com"
    response = client.post(
        "/v1/auth/signup",
        json={
            "email": email,
            "password": "StrongPass123",
            "preferred_name": "Preferences",
            "onboarding": {
                "fitnessGoal": "build-muscle",
                "experienceLevel": "beginner",
                "workoutFrequency": "4-days",
                "workoutSplit": "upper-lower",
                "trainingPlace": "large-gym",
                "selectedEquipment": ["olympic_barbell", "flat_bench"],
            },
        },
    )
    assert response.status_code == 201
    return {**response.json(), "email": email}


def _headers(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_training_preferences_require_authentication():
    assert client.get("/v1/users/me/training-preferences").status_code == 401
    assert client.patch("/v1/users/me/training-preferences", json={}).status_code == 401


def test_frequency_change_keeps_completed_history_without_reusing_schedule_slot():
    tokens = _signup()
    headers = _headers(tokens)
    before = client.get("/v1/workouts/week", headers=headers)
    assert before.status_code == 200
    assert before.json()["daysPerWeek"] == 4
    completed_day = before.json()["workouts"][0]
    completed_snapshot = completed_day
    session = client.post(
        "/v1/workouts/sessions",
        headers=headers,
        json={
            "workoutDayId": completed_day["workoutDayId"],
            "workoutDate": completed_day["date"],
            "dayType": completed_day["dayType"],
        },
    )
    assert session.status_code == 201
    assert client.patch(
        f"/v1/workouts/sessions/{session.json()['id']}/complete", headers=headers
    ).status_code == 200

    changed = client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={"workoutFrequency": "1-day", "fitnessGoal": "get-stronger"},
    )
    assert changed.status_code == 200
    assert changed.json()["workoutFrequency"] == "1-day"
    assert changed.json()["fitnessGoal"] == "get-stronger"
    assert changed.json()["workoutSplit"] == "upper-lower"

    after = client.get("/v1/workouts/week", headers=headers)
    assert after.status_code == 200
    assert after.json()["daysPerWeek"] == 1
    assert [workout["date"] for workout in after.json()["workouts"]] == _scheduled_dates(
        before.json()["weekStart"], (2,)
    )
    assert completed_day["workoutDayId"] not in {
        workout["workoutDayId"] for workout in after.json()["workouts"]
    }
    with SessionLocal() as db:
        user = db.query(User).filter_by(email=tokens["email"]).one()
        plan = db.query(WorkoutWeekPlan).filter_by(
            user_id=str(user.id),
            week_start_date=after.json()["weekStart"],
        ).one()
        snapshots = plan.plan_json["completedWorkoutSnapshots"]
    assert len(snapshots) == 1
    assert snapshots[0]["workoutDayId"] == completed_snapshot["workoutDayId"]
    assert snapshots[0]["date"] == completed_snapshot["date"]
    history = client.get("/v1/workouts/sessions", headers=headers)
    assert history.status_code == 200
    assert history.json()["total"] == 1


def test_frequency_change_from_one_to_four_uses_four_day_schedule():
    tokens = _signup()
    headers = _headers(tokens)
    assert client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={"workoutFrequency": "1-day"},
    ).status_code == 200
    one_day = client.get("/v1/workouts/week", headers=headers)
    assert [workout["date"] for workout in one_day.json()["workouts"]] == _scheduled_dates(
        one_day.json()["weekStart"], (2,)
    )
    completed_day = one_day.json()["workouts"][0]
    session = client.post(
        "/v1/workouts/sessions",
        headers=headers,
        json={
            "workoutDayId": completed_day["workoutDayId"],
            "workoutDate": completed_day["date"],
            "dayType": completed_day["dayType"],
        },
    )
    assert session.status_code == 201
    assert client.patch(
        f"/v1/workouts/sessions/{session.json()['id']}/complete", headers=headers
    ).status_code == 200

    assert client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={"workoutFrequency": "4-days"},
    ).status_code == 200
    four_days = client.get("/v1/workouts/week", headers=headers)
    assert [workout["date"] for workout in four_days.json()["workouts"]] == _scheduled_dates(
        four_days.json()["weekStart"], (0, 1, 3, 5)
    )
    assert completed_day["workoutDayId"] not in {
        workout["workoutDayId"] for workout in four_days.json()["workouts"]
    }


def test_legacy_preferences_are_canonicalized_without_profile_overwrites():
    tokens = _signup()
    headers = _headers(tokens)
    legacy = {
        "fitness_goal": "hypertrophy",
        "experience_level": "novice",
        "days_per_week": 3,
        "split_preference": "ppl",
        "training_place": "large_gym",
        "equipment_ids": ["dumbbells"],
    }
    with SessionLocal() as db:
        user = db.query(User).filter_by(email=tokens["email"]).one()
        user_id = str(user.id)
        user.onboarding_profile.data = legacy
        db.commit()

    response = client.get("/v1/users/me/training-preferences", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "fitnessGoal": "build-muscle",
        "experienceLevel": "no-experience",
        "workoutFrequency": "3-days",
        "workoutSplit": "push-pull-legs",
        "trainingPlace": "large-gym",
        "selectedEquipment": ["dumbbells"],
    }
    assert client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={"fitnessGoal": "get-stronger"},
    ).status_code == 200
    with SessionLocal() as db:
        profile = db.query(User).filter_by(id=user_id).one().onboarding_profile
        assert profile.data["fitnessGoal"] == "get-stronger"
        assert profile.data["days_per_week"] == 3
        assert "workoutFrequency" not in profile.data


def test_custom_split_requires_a_workout():
    tokens = _signup()
    response = client.patch(
        "/v1/users/me/training-preferences",
        headers=_headers(tokens),
        json={"workoutSplit": "custom", "customWorkouts": []},
    )
    assert response.status_code == 422


def test_merged_custom_split_stays_valid_when_updating_another_field():
    tokens = _signup()
    headers = _headers(tokens)
    custom_cycle = [{
        "id": "custom-push",
        "name": "Push",
        "type": "custom",
        "muscleGroups": ["chest", "shoulders", "triceps"],
    }]
    assert client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={"workoutSplit": "custom", "customWorkouts": custom_cycle},
    ).status_code == 200

    response = client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={"fitnessGoal": "get-stronger"},
    )
    assert response.status_code == 200
    assert response.json()["workoutSplit"] == "custom"
    assert response.json()["customWorkouts"] == custom_cycle


def test_noop_preference_update_keeps_current_plan():
    tokens = _signup()
    headers = _headers(tokens)
    before = client.get("/v1/workouts/week", headers=headers)
    assert before.status_code == 200
    before_ids = [workout["workoutDayId"] for workout in before.json()["workouts"]]

    response = client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={"fitnessGoal": "build-muscle"},
    )
    assert response.status_code == 200
    after = client.get("/v1/workouts/week", headers=headers)
    assert after.status_code == 200
    assert [workout["workoutDayId"] for workout in after.json()["workouts"]] == before_ids


def test_preferences_reject_unknown_equipment_and_invalid_known_values():
    tokens = _signup()
    headers = _headers(tokens)
    assert client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={"selectedEquipment": ["not-in-the-catalog"]},
    ).status_code == 422
    assert client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={"workoutFrequency": "four-days"},
    ).status_code == 422
    response = client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={"selectedEquipment": ["olympic_barbell", "flat_bench"]},
    )
    assert response.status_code == 200
    assert response.json()["selectedEquipment"] == ["olympic_barbell", "flat_bench"]
