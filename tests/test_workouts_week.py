"""
Tests for weekly workout plan endpoints:
- GET /v1/workouts/week
- POST /v1/workouts/week/skip
- PATCH /v1/workouts/week/duration

Note: These tests assume migrations have been run and seed data exists in the dev DB.
"""
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _signup_and_get_token(email: str) -> str:
    """Helper to create a user and get auth token."""
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
    """Helper to create auth headers."""
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# GET /v1/workouts/week tests
# ============================================================================


def test_week_requires_auth():
    """GET /v1/workouts/week without auth should return 401."""
    response = client.get("/v1/workouts/week")
    assert response.status_code == 401


def test_week_returns_plan_with_correct_structure():
    """Logged-in user should receive a weekly plan with correct structure."""
    token = _signup_and_get_token("week_structure1@example.com")
    headers = _auth_headers(token)

    # Set onboarding with days_per_week
    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells", "flat_bench"],
            "split_preference": "upper_lower",
            "days_per_week": 4,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # Get weekly plan
    response = client.get("/v1/workouts/week", headers=headers)
    assert response.status_code == 200

    data = response.json()

    # Validate top-level structure
    assert "weekStart" in data
    assert "daysPerWeek" in data
    assert data["daysPerWeek"] == 4
    assert "generatedAt" in data
    assert "seed" in data
    assert "workouts" in data
    assert len(data["workouts"]) == 4


def test_week_workout_day_structure():
    """Each workout day should have the correct structure."""
    token = _signup_and_get_token("week_day_structure1@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "split_preference": "upper_lower",
            "days_per_week": 3,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    response = client.get("/v1/workouts/week", headers=headers)
    assert response.status_code == 200

    data = response.json()
    workouts = data["workouts"]

    for i, workout in enumerate(workouts):
        assert "workoutDayId" in workout, f"Workout {i} missing workoutDayId"
        assert "slotIndex" in workout, f"Workout {i} missing slotIndex"
        assert workout["slotIndex"] == i, f"Workout {i} has wrong slotIndex"
        assert "date" in workout, f"Workout {i} missing date"
        assert "durationMinutes" in workout, f"Workout {i} missing durationMinutes"
        assert "title" in workout, f"Workout {i} missing title"
        assert "splitKey" in workout, f"Workout {i} missing splitKey"
        assert "dayType" in workout, f"Workout {i} missing dayType"
        assert "estimatedMinutes" in workout, f"Workout {i} missing estimatedMinutes"
        assert "exerciseBlocks" in workout, f"Workout {i} missing exerciseBlocks"


def test_week_days_per_week_2():
    """User with days_per_week=2 should get 2 workouts."""
    token = _signup_and_get_token("week_days2@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "days_per_week": 2,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    response = client.get("/v1/workouts/week", headers=headers)
    assert response.status_code == 200

    data = response.json()
    assert data["daysPerWeek"] == 2
    assert len(data["workouts"]) == 2


def test_week_days_per_week_5():
    """User with days_per_week=5 should get 5 workouts."""
    token = _signup_and_get_token("week_days5@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "days_per_week": 5,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    response = client.get("/v1/workouts/week", headers=headers)
    assert response.status_code == 200

    data = response.json()
    assert data["daysPerWeek"] == 5
    assert len(data["workouts"]) == 5


def test_week_deterministic_without_force():
    """Same user calling GET /week without force should get same plan."""
    token = _signup_and_get_token("week_deterministic1@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "days_per_week": 3,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # First call
    response1 = client.get("/v1/workouts/week", headers=headers)
    assert response1.status_code == 200
    data1 = response1.json()

    # Second call
    response2 = client.get("/v1/workouts/week", headers=headers)
    assert response2.status_code == 200
    data2 = response2.json()

    # Should have same workoutDayIds
    ids1 = [w["workoutDayId"] for w in data1["workouts"]]
    ids2 = [w["workoutDayId"] for w in data2["workouts"]]
    assert ids1 == ids2, "Plan should be stable without force"


def test_week_force_regenerates_with_new_ids():
    """GET /week?force=true should generate new workoutDayIds."""
    token = _signup_and_get_token("week_force_regen1@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "days_per_week": 3,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # First call (creates plan)
    response1 = client.get("/v1/workouts/week", headers=headers)
    assert response1.status_code == 200
    data1 = response1.json()
    ids1 = [w["workoutDayId"] for w in data1["workouts"]]

    # Force regenerate
    response2 = client.get("/v1/workouts/week?force=true", headers=headers)
    assert response2.status_code == 200
    data2 = response2.json()
    ids2 = [w["workoutDayId"] for w in data2["workouts"]]

    # Should have different workoutDayIds
    assert ids1 != ids2, "Force regen should create new workoutDayIds"


# ============================================================================
# POST /v1/workouts/week/skip tests
# ============================================================================


def test_skip_requires_auth():
    """POST /v1/workouts/week/skip without auth should return 401."""
    response = client.post(
        "/v1/workouts/week/skip",
        json={"workoutDayId": "some-id"},
    )
    assert response.status_code == 401


def test_skip_removes_and_appends():
    """Skipping a workout should remove it and append a new one."""
    token = _signup_and_get_token("week_skip1@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "split_preference": "upper_lower",
            "days_per_week": 4,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # Get initial plan
    response = client.get("/v1/workouts/week", headers=headers)
    assert response.status_code == 200
    initial_plan = response.json()
    initial_ids = [w["workoutDayId"] for w in initial_plan["workouts"]]
    initial_count = len(initial_plan["workouts"])

    # Skip the first workout
    skip_id = initial_ids[0]
    response = client.post(
        "/v1/workouts/week/skip",
        json={"workoutDayId": skip_id},
        headers=headers,
    )
    assert response.status_code == 200

    updated_plan = response.json()
    updated_ids = [w["workoutDayId"] for w in updated_plan["workouts"]]

    # Length should remain the same
    assert len(updated_plan["workouts"]) == initial_count

    # Skipped ID should no longer exist
    assert skip_id not in updated_ids

    # A new ID should be at the end
    assert updated_ids[-1] not in initial_ids


def test_skip_maintains_slot_indices():
    """After skip, slotIndex should be normalized 0..N-1."""
    token = _signup_and_get_token("week_skip_slots1@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "days_per_week": 4,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # Get initial plan
    response = client.get("/v1/workouts/week", headers=headers)
    initial_plan = response.json()
    skip_id = initial_plan["workouts"][1]["workoutDayId"]  # Skip second workout

    # Skip
    response = client.post(
        "/v1/workouts/week/skip",
        json={"workoutDayId": skip_id},
        headers=headers,
    )
    assert response.status_code == 200

    updated_plan = response.json()

    # Check slotIndex is normalized
    for i, workout in enumerate(updated_plan["workouts"]):
        assert workout["slotIndex"] == i, f"Workout {i} has wrong slotIndex after skip"


def test_skip_follows_rotation():
    """New workout appended after skip should follow split rotation."""
    token = _signup_and_get_token("week_skip_rotation1@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "split_preference": "upper_lower",
            "days_per_week": 4,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # Get initial plan
    response = client.get("/v1/workouts/week", headers=headers)
    initial_plan = response.json()
    skip_id = initial_plan["workouts"][0]["workoutDayId"]

    # Skip the first workout
    response = client.post(
        "/v1/workouts/week/skip",
        json={"workoutDayId": skip_id},
        headers=headers,
    )
    assert response.status_code == 200

    updated_plan = response.json()
    last_workout = updated_plan["workouts"][-1]
    second_to_last = updated_plan["workouts"][-2]

    # Verify rotation (upper_lower alternates)
    if second_to_last["dayType"] == "upper":
        assert last_workout["dayType"] == "lower"
    else:
        assert last_workout["dayType"] == "upper"


def test_skip_invalid_workout_day_id():
    """Skipping a non-existent workoutDayId should return 404."""
    token = _signup_and_get_token("week_skip_invalid1@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "days_per_week": 3,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # Create initial plan
    response = client.get("/v1/workouts/week", headers=headers)
    assert response.status_code == 200

    # Try to skip with invalid ID
    response = client.post(
        "/v1/workouts/week/skip",
        json={"workoutDayId": "non-existent-id"},
        headers=headers,
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


# ============================================================================
# PATCH /v1/workouts/week/duration tests
# ============================================================================


def test_duration_requires_auth():
    """PATCH /v1/workouts/week/duration without auth should return 401."""
    response = client.patch(
        "/v1/workouts/week/duration",
        json={"workoutDayId": "some-id", "durationMinutes": 45},
    )
    assert response.status_code == 401


def test_duration_updates_and_regenerates():
    """Updating duration should update the workout and regenerate exercises."""
    token = _signup_and_get_token("week_duration1@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells", "flat_bench", "olympic_barbell", "plates"],
            "days_per_week": 3,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # Get initial plan
    response = client.get("/v1/workouts/week", headers=headers)
    assert response.status_code == 200
    initial_plan = response.json()
    workout_id = initial_plan["workouts"][0]["workoutDayId"]
    initial_duration = initial_plan["workouts"][0]["durationMinutes"]

    # Update duration to something different
    new_duration = 60 if initial_duration < 50 else 25
    response = client.patch(
        "/v1/workouts/week/duration",
        json={"workoutDayId": workout_id, "durationMinutes": new_duration},
        headers=headers,
    )
    assert response.status_code == 200

    updated_plan = response.json()
    updated_workout = next(
        w for w in updated_plan["workouts"] if w["workoutDayId"] == workout_id
    )

    assert updated_workout["durationMinutes"] == new_duration


def test_duration_preserves_workout_day_ids():
    """Duration update should preserve all workoutDayIds."""
    token = _signup_and_get_token("week_duration_ids1@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "days_per_week": 4,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # Get initial plan
    response = client.get("/v1/workouts/week", headers=headers)
    initial_plan = response.json()
    initial_ids = [w["workoutDayId"] for w in initial_plan["workouts"]]
    workout_id = initial_ids[0]

    # Update duration
    response = client.patch(
        "/v1/workouts/week/duration",
        json={"workoutDayId": workout_id, "durationMinutes": 45},
        headers=headers,
    )
    assert response.status_code == 200

    updated_plan = response.json()
    updated_ids = [w["workoutDayId"] for w in updated_plan["workouts"]]

    # IDs should be preserved
    assert initial_ids == updated_ids


def test_duration_preserves_all_durations():
    """Duration update should preserve durations for all workouts."""
    token = _signup_and_get_token("week_duration_preserve1@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "days_per_week": 3,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # Get initial plan
    response = client.get("/v1/workouts/week", headers=headers)
    initial_plan = response.json()

    # Update first workout's duration
    first_id = initial_plan["workouts"][0]["workoutDayId"]
    second_duration = initial_plan["workouts"][1]["durationMinutes"]

    response = client.patch(
        "/v1/workouts/week/duration",
        json={"workoutDayId": first_id, "durationMinutes": 60},
        headers=headers,
    )
    assert response.status_code == 200

    updated_plan = response.json()

    # Second workout's duration should be unchanged
    assert updated_plan["workouts"][1]["durationMinutes"] == second_duration


def test_duration_invalid_workout_day_id():
    """Updating duration for non-existent workoutDayId should return 404."""
    token = _signup_and_get_token("week_duration_invalid1@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "days_per_week": 3,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # Create initial plan
    response = client.get("/v1/workouts/week", headers=headers)
    assert response.status_code == 200

    # Try to update with invalid ID
    response = client.patch(
        "/v1/workouts/week/duration",
        json={"workoutDayId": "non-existent-id", "durationMinutes": 45},
        headers=headers,
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_duration_affects_exercise_count():
    """Longer duration should result in more exercises."""
    token = _signup_and_get_token("week_duration_count1@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells", "flat_bench", "olympic_barbell", "plates", "pull_up_bar"],
            "days_per_week": 2,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # Get initial plan
    response = client.get("/v1/workouts/week", headers=headers)
    initial_plan = response.json()
    workout_id = initial_plan["workouts"][0]["workoutDayId"]

    # Set to short duration (25 min = 3 exercises)
    response = client.patch(
        "/v1/workouts/week/duration",
        json={"workoutDayId": workout_id, "durationMinutes": 25},
        headers=headers,
    )
    assert response.status_code == 200
    short_plan = response.json()
    short_workout = next(w for w in short_plan["workouts"] if w["workoutDayId"] == workout_id)
    short_exercise_count = sum(len(b["items"]) for b in short_workout["exerciseBlocks"])

    # Set to long duration (60 min = 7 exercises)
    response = client.patch(
        "/v1/workouts/week/duration",
        json={"workoutDayId": workout_id, "durationMinutes": 60},
        headers=headers,
    )
    assert response.status_code == 200
    long_plan = response.json()
    long_workout = next(w for w in long_plan["workouts"] if w["workoutDayId"] == workout_id)
    long_exercise_count = sum(len(b["items"]) for b in long_workout["exerciseBlocks"])

    # Longer duration should have more exercises
    assert long_exercise_count > short_exercise_count


# ============================================================================
# Integration: /next wrapper tests
# ============================================================================


def test_next_creates_week_plan():
    """POST /next should create a week plan if none exists."""
    token = _signup_and_get_token("week_next_creates1@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "days_per_week": 3,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # Call /next first (should create week plan internally)
    response = client.post("/v1/workouts/next", headers=headers)
    assert response.status_code == 200
    next_workout = response.json()

    # Then call /week to verify plan exists
    response = client.get("/v1/workouts/week", headers=headers)
    assert response.status_code == 200
    week_plan = response.json()

    # /next workout should be one from the week plan
    week_ids = [w["workoutDayId"] for w in week_plan["workouts"]]
    assert next_workout["workoutId"] in week_ids


def test_next_returns_workout_from_week_plan():
    """POST /next should return a workout from the existing week plan."""
    token = _signup_and_get_token("week_next_from_plan1@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "days_per_week": 4,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # Create week plan first
    response = client.get("/v1/workouts/week", headers=headers)
    week_plan = response.json()
    week_ids = [w["workoutDayId"] for w in week_plan["workouts"]]

    # Call /next
    response = client.post("/v1/workouts/next", headers=headers)
    assert response.status_code == 200
    next_workout = response.json()

    # Workout ID should be from the week plan
    assert next_workout["workoutId"] in week_ids


def test_next_force_regenerates_week():
    """POST /next?force=true should regenerate the week plan first."""
    token = _signup_and_get_token("week_next_force1@example.com")
    headers = _auth_headers(token)

    onboarding_payload = {
        "data": {
            "equipment_ids": ["dumbbells"],
            "days_per_week": 3,
        },
        "is_complete": True,
    }
    response = client.post("/v1/onboarding/me", json=onboarding_payload, headers=headers)
    assert response.status_code == 200

    # Create initial week plan
    response = client.get("/v1/workouts/week", headers=headers)
    initial_plan = response.json()
    initial_ids = [w["workoutDayId"] for w in initial_plan["workouts"]]

    # Call /next with force
    response = client.post("/v1/workouts/next?force=true", headers=headers)
    assert response.status_code == 200
    next_workout = response.json()

    # Get week plan again
    response = client.get("/v1/workouts/week", headers=headers)
    new_plan = response.json()
    new_ids = [w["workoutDayId"] for w in new_plan["workouts"]]

    # IDs should be different (force regenerated)
    assert initial_ids != new_ids

    # /next workout should be from the new plan
    assert next_workout["workoutId"] in new_ids

