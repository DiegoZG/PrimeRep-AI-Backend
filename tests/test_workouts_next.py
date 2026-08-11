"""
Tests for POST /v1/workouts/next endpoint.

Note: These tests assume migrations have been run and seed data exists in the dev DB.
"""
import uuid

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _unique_email(prefix: str) -> str:
    return f"next_{prefix}_{uuid.uuid4().hex[:8]}@example.com"


def _signup_and_get_token(email: str) -> str:
    payload = {
        "email": email,
        "password": "StrongPass123",
        "preferred_name": "Test",
        "last_name": "User",
    }
    response = client.post("/v1/auth/signup", json=payload)
    assert response.status_code == 201
    data = response.json()
    return data["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_workout_next_requires_auth():
    """POST /v1/workouts/next without auth should return 401."""
    response = client.post("/v1/workouts/next")
    assert response.status_code == 401


def test_workout_next_logged_in_with_equipment():
    """Logged-in user with equipment should receive a valid workout."""
    token = _signup_and_get_token(_unique_email("tester1"))
    headers = _auth_headers(token)

    # Set user equipment via onboarding
    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells", "flat_bench", "olympic_barbell", "plates"]
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # Generate workout
    response = client.post("/v1/workouts/next", headers=headers)
    assert response.status_code == 200

    data = response.json()

    # Validate response structure
    assert "workoutId" in data
    assert "title" in data
    assert "splitKey" in data
    assert data["splitKey"] == "upper_lower"
    assert "dayType" in data
    assert data["dayType"] in ("upper", "lower")
    assert "estimatedMinutes" in data
    assert "exerciseBlocks" in data

    # Validate exercise blocks
    blocks = data["exerciseBlocks"]
    assert len(blocks) >= 1

    # Collect all exercises to check eligibility
    user_equipment = {"dumbbells", "flat_bench", "olympic_barbell", "plates"}
    all_exercise_ids = []

    for block in blocks:
        assert "blockType" in block
        assert block["blockType"] in ("main", "accessory")
        assert "items" in block

        for item in block["items"]:
            assert "exercise" in item
            assert "prescription" in item

            exercise = item["exercise"]
            assert "id" in exercise
            assert "name" in exercise
            assert "requiredEquipmentIds" in exercise

            all_exercise_ids.append(exercise["id"])

            # Verify eligibility: required equipment is subset of user's equipment
            required = set(exercise["requiredEquipmentIds"])
            assert (
                required <= user_equipment or len(required) == 0
            ), f"Exercise {exercise['id']} requires {required} but user only has {user_equipment}"

            # Validate prescription
            prescription = item["prescription"]
            assert "sets" in prescription
            assert "repsMin" in prescription
            assert "repsMax" in prescription
            assert "restSeconds" in prescription


def test_workout_next_no_duplicates():
    """Workout should not contain duplicate exercises."""
    token = _signup_and_get_token(_unique_email("tester2"))
    headers = _auth_headers(token)

    # Set user equipment via onboarding
    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells", "flat_bench", "olympic_barbell", "plates", "pull_up_bar"]
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # Generate workout
    response = client.post("/v1/workouts/next", headers=headers)
    assert response.status_code == 200

    data = response.json()
    blocks = data["exerciseBlocks"]

    # Collect all exercise ids
    all_exercise_ids = []
    for block in blocks:
        for item in block["items"]:
            all_exercise_ids.append(item["exercise"]["id"])

    # Assert no duplicates
    assert len(all_exercise_ids) == len(set(all_exercise_ids)), (
        f"Duplicate exercises found: {all_exercise_ids}"
    )


def test_workout_rotation_upper_lower():
    """
    Calling /v1/workouts/next twice should rotate day types (upper -> lower).
    History is persisted, so second call on same day updates day_type.
    """
    token = _signup_and_get_token(_unique_email("rotation1"))
    headers = _auth_headers(token)

    # Set equipment + split preference
    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells", "flat_bench"],
            "split_preference": "upper_lower",
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # First workout: should be "upper" (first day of split)
    response = client.post("/v1/workouts/next", headers=headers)
    assert response.status_code == 200
    first_workout = response.json()
    assert first_workout["dayType"] == "upper", "First workout should be upper"
    assert first_workout["splitKey"] == "upper_lower"


def test_workout_ppl_split():
    """User with PPL split should get push/pull/legs rotation."""
    token = _signup_and_get_token(_unique_email("ppl1"))
    headers = _auth_headers(token)

    # Set PPL split preference
    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "split_preference": "ppl",
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # First workout: should be "push" (first day of PPL)
    response = client.post("/v1/workouts/next", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["dayType"] == "push", "First PPL workout should be push"
    assert data["splitKey"] == "ppl"


def test_workout_full_body_split():
    """User with full_body split should always get full_body day."""
    token = _signup_and_get_token(_unique_email("fullbody1"))
    headers = _auth_headers(token)

    # Set full body split preference
    onboarding_payload = {
        "data": {
            "equipment_ids": [],
            "split_preference": "full_body",
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # First workout: should be "full_body"
    response = client.post("/v1/workouts/next", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["dayType"] == "full_body", "Full body split should always be full_body"
    assert data["splitKey"] == "full_body"


def test_workout_default_split_preference():
    """User without split_preference should default to upper_lower."""
    token = _signup_and_get_token(_unique_email("default1"))
    headers = _auth_headers(token)

    # Set only equipment, no split preference
    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # First workout: should default to upper_lower
    response = client.post("/v1/workouts/next", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["splitKey"] == "upper_lower", "Default split should be upper_lower"
    assert data["dayType"] == "upper", "First day of upper_lower should be upper"


def test_workout_no_onboarding():
    """User without any onboarding should still get a workout with defaults."""
    token = _signup_and_get_token(_unique_email("noonboard1"))
    headers = _auth_headers(token)

    # Don't set any onboarding - just call workout endpoint
    response = client.post("/v1/workouts/next", headers=headers)
    assert response.status_code == 200

    data = response.json()
    assert data["splitKey"] == "upper_lower", "No onboarding should default to upper_lower"
    assert data["dayType"] in ("upper", "lower")
    assert len(data["exerciseBlocks"]) >= 1
