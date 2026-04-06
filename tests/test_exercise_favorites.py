"""
Tests for exercise favorites endpoints.

Note: These tests assume migrations have been run and seed data exists in the dev DB.
"""
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


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


def test_favorite_exercise_idempotent():
    """Favoriting the same exercise twice should be idempotent."""
    token = _signup_and_get_token("favtester1@example.com")
    headers = _auth_headers(token)

    # First favorite
    response = client.post("/v1/exercises/bench_press/favorite", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["exercise_id"] == "bench_press"
    assert data["is_favorited"] is True

    # Second favorite should be no-op but still report favorited
    response = client.post("/v1/exercises/bench_press/favorite", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["exercise_id"] == "bench_press"
    assert data["is_favorited"] is True

    # Ensure it appears in favorites list
    response = client.get("/v1/exercises/favorites", headers=headers)
    assert response.status_code == 200
    items = response.json()["items"]
    ids = [item["id"] for item in items]
    assert "bench_press" in ids


def test_unfavorite_exercise_idempotent():
    """Unfavoriting should be idempotent and remove from favorites list."""
    token = _signup_and_get_token("favtester2@example.com")
    headers = _auth_headers(token)

    # Ensure favorited first
    response = client.post("/v1/exercises/bench_press/favorite", headers=headers)
    assert response.status_code == 200

    # First unfavorite
    response = client.delete("/v1/exercises/bench_press/favorite", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["exercise_id"] == "bench_press"
    assert data["is_favorited"] is False

    # Second unfavorite should still succeed and remain false
    response = client.delete("/v1/exercises/bench_press/favorite", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["exercise_id"] == "bench_press"
    assert data["is_favorited"] is False

    # Ensure it's not in favorites list
    response = client.get("/v1/exercises/favorites", headers=headers)
    assert response.status_code == 200
    items = response.json()["items"]
    ids = [item["id"] for item in items]
    assert "bench_press" not in ids


