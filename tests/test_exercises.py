"""
Smoke tests for /v1/exercises endpoints.

Note: These tests assume migrations have been run and seed data exists in the dev DB.
"""
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_get_exercises_returns_200_and_items():
    """GET /v1/exercises should return 200 and a non-empty items list."""
    response = client.get("/v1/exercises")
    assert response.status_code == 200

    data = response.json()
    assert "items" in data
    assert isinstance(data["items"], list)
    assert len(data["items"]) > 0


def test_exercise_items_have_required_fields():
    """Exercise items should have core fields."""
    response = client.get("/v1/exercises")
    assert response.status_code == 200

    items = response.json()["items"]
    assert len(items) > 0
    item = items[0]

    assert "id" in item
    assert "name" in item
    assert "exercise_type" in item
    assert "primary_muscle" in item
    assert "secondary_muscles" in item
    assert "required_equipment_ids" in item
    assert "is_active" in item


def test_known_seeded_exercise_exists():
    """Known seeded exercise bench_press should exist."""
    response = client.get("/v1/exercises")
    assert response.status_code == 200

    items = response.json()["items"]
    ids = [item["id"] for item in items]
    assert "bench_press" in ids


