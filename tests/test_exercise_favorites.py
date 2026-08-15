"""
Tests for exercise favorites endpoints.

Note: These tests assume migrations have been run and seed data exists in the dev DB.
"""
import uuid

from fastapi.testclient import TestClient

import app.api.v1.exercises.router as exercises_router
from app.main import app


client = TestClient(app)


def _unique_email(prefix: str) -> str:
    return f"fav_{prefix}_{uuid.uuid4().hex[:8]}@example.com"


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
    token = _signup_and_get_token(_unique_email("tester1"))
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
    token = _signup_and_get_token(_unique_email("tester2"))
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


def test_catalog_and_detail_include_authenticated_favorite_state():
    token = _signup_and_get_token(_unique_email("contract"))
    headers = _auth_headers(token)

    response = client.post("/v1/exercises/bench_press/favorite", headers=headers)
    assert response.status_code == 200

    response = client.get("/v1/exercises", headers=headers)
    assert response.status_code == 200
    states = {item["id"]: item["is_favorited"] for item in response.json()["items"]}
    assert states["bench_press"] is True
    assert states["incline_dumbbell_press"] is False

    response = client.get("/v1/exercises/bench_press", headers=headers)
    assert response.status_code == 200
    assert response.json()["is_favorited"] is True

    other_token = _signup_and_get_token(_unique_email("other_user"))
    response = client.get(
        "/v1/exercises/bench_press", headers=_auth_headers(other_token)
    )
    assert response.status_code == 200
    assert response.json()["is_favorited"] is False


def test_anonymous_catalog_and_detail_return_false():
    response = client.get("/v1/exercises")
    assert response.status_code == 200
    assert all(item["is_favorited"] is False for item in response.json()["items"])

    response = client.get("/v1/exercises/bench_press")
    assert response.status_code == 200
    assert response.json()["is_favorited"] is False


def test_invalid_optional_auth_token_is_rejected():
    headers = _auth_headers("not-a-valid-token")

    assert client.get("/v1/exercises", headers=headers).status_code == 401
    assert client.get("/v1/exercises/bench_press", headers=headers).status_code == 401
    assert (
        client.get("/v1/exercises", headers={"Authorization": "invalid"}).status_code
        == 401
    )


def test_catalog_uses_one_bulk_favorite_lookup(monkeypatch):
    token = _signup_and_get_token(_unique_email("bulk_lookup"))
    calls: list[list[str]] = []
    original = exercises_router.list_favorite_ids

    def track_bulk_lookup(db, user_id: str, exercise_ids: list[str]) -> set[str]:
        calls.append(exercise_ids)
        return original(db, user_id, exercise_ids)

    monkeypatch.setattr(exercises_router, "list_favorite_ids", track_bulk_lookup)

    response = client.get("/v1/exercises", headers=_auth_headers(token))
    assert response.status_code == 200
    assert len(calls) == 1
    assert len(calls[0]) == len(response.json()["items"])
