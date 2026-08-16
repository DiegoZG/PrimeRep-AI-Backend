import uuid
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from app.core.equipment_weights_service import _sanitize_legacy_weights
from app.main import app


client = TestClient(app)


def _signup(prefix: str, onboarding: Optional[dict] = None) -> str:
    payload = {
        "email": f"weights_{prefix}_{uuid.uuid4().hex[:8]}@example.com",
        "password": "StrongPass123",
        "preferred_name": "Test",
        "last_name": "User",
    }
    if onboarding is not None:
        payload["onboarding"] = onboarding
    response = client.post("/v1/auth/signup", json=payload)
    assert response.status_code == 201
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_equipment_weights_require_authentication():
    assert client.get("/v1/users/me/equipment-weights").status_code == 401
    assert client.put(
        "/v1/users/me/equipment-weights",
        json={"dumbbellWeights": [], "plateWeights": []},
    ).status_code == 401


def test_absent_equipment_weights_return_empty_arrays():
    token = _signup("absent")

    response = client.get("/v1/users/me/equipment-weights", headers=_auth(token))

    assert response.status_code == 200
    assert response.json() == {"dumbbellWeights": [], "plateWeights": []}


def test_equipment_weights_create_and_overwrite_complete_inventory():
    token = _signup("roundtrip")
    headers = _auth(token)

    created = client.put(
        "/v1/users/me/equipment-weights",
        headers=headers,
        json={"dumbbellWeights": [5, 10, 15], "plateWeights": [2.5, 5]},
    )
    assert created.status_code == 200
    assert created.json() == {
        "dumbbellWeights": [5.0, 10.0, 15.0],
        "plateWeights": [2.5, 5.0],
    }

    overwritten = client.put(
        "/v1/users/me/equipment-weights",
        headers=headers,
        json={"dumbbellWeights": [20], "plateWeights": []},
    )
    assert overwritten.status_code == 200
    assert client.get(
        "/v1/users/me/equipment-weights", headers=headers
    ).json() == {"dumbbellWeights": [20.0], "plateWeights": []}


def test_equipment_weights_reject_negative_and_non_numeric_values():
    token = _signup("invalid")
    headers = _auth(token)

    negative = client.put(
        "/v1/users/me/equipment-weights",
        headers=headers,
        json={"dumbbellWeights": [-5], "plateWeights": []},
    )
    non_numeric = client.put(
        "/v1/users/me/equipment-weights",
        headers=headers,
        json={"dumbbellWeights": [], "plateWeights": ["five"]},
    )

    assert negative.status_code == 422
    assert non_numeric.status_code == 422


def test_equipment_weights_are_isolated_by_user():
    first_token = _signup("owner")
    second_token = _signup("other")
    client.put(
        "/v1/users/me/equipment-weights",
        headers=_auth(first_token),
        json={"dumbbellWeights": [55], "plateWeights": [45]},
    )

    response = client.get(
        "/v1/users/me/equipment-weights", headers=_auth(second_token)
    )

    assert response.json() == {"dumbbellWeights": [], "plateWeights": []}


def test_signup_seeds_equipment_weights_from_onboarding():
    token = _signup(
        "seeded",
        {"dumbbellWeights": [12.5, 25], "plateWeights": [5, 45]},
    )

    response = client.get("/v1/users/me/equipment-weights", headers=_auth(token))

    assert response.json() == {
        "dumbbellWeights": [12.5, 25.0],
        "plateWeights": [5.0, 45.0],
    }


def test_signup_without_weight_selections_returns_empty_arrays():
    token = _signup("no_selections", {"fitnessGoal": "strength"})

    response = client.get("/v1/users/me/equipment-weights", headers=_auth(token))

    assert response.json() == {"dumbbellWeights": [], "plateWeights": []}


def test_signup_rejects_invalid_onboarding_weights():
    response = client.post(
        "/v1/auth/signup",
        json={
            "email": f"weights_invalid_signup_{uuid.uuid4().hex[:8]}@example.com",
            "password": "StrongPass123",
            "preferred_name": "Test",
            "onboarding": {"dumbbellWeights": [-2.5], "plateWeights": []},
        },
    )

    assert response.status_code == 422


def test_legacy_onboarding_weights_are_used_until_first_write():
    token = _signup("legacy")
    headers = _auth(token)
    onboarding = client.post(
        "/v1/onboarding/me",
        headers=headers,
        json={
            "data": {"dumbbellWeights": [7.5], "plateWeights": [2.5]},
            "is_complete": True,
        },
    )
    assert onboarding.status_code == 200

    fallback = client.get("/v1/users/me/equipment-weights", headers=headers)
    assert fallback.json() == {
        "dumbbellWeights": [7.5],
        "plateWeights": [2.5],
    }

    client.put(
        "/v1/users/me/equipment-weights",
        headers=headers,
        json={"dumbbellWeights": [], "plateWeights": []},
    )
    authoritative = client.get("/v1/users/me/equipment-weights", headers=headers)
    assert authoritative.json() == {"dumbbellWeights": [], "plateWeights": []}


def test_invalid_legacy_onboarding_weight_arrays_are_ignored():
    token = _signup("legacy_invalid")
    headers = _auth(token)
    onboarding = client.post(
        "/v1/onboarding/me",
        headers=headers,
        json={
            "data": {
                "dumbbellWeights": [5, True, None],
                "plateWeights": "45",
            },
            "is_complete": True,
        },
    )
    assert onboarding.status_code == 200

    response = client.get("/v1/users/me/equipment-weights", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"dumbbellWeights": [], "plateWeights": []}


@pytest.mark.parametrize(
    "values",
    [None, True, "5", {}, [-1], [float("nan")], [float("inf")]],
)
def test_legacy_weight_sanitizer_rejects_invalid_values(values):
    assert _sanitize_legacy_weights(values) == []


def test_signup_rolls_back_all_rows_when_weight_seed_fails(monkeypatch):
    email = f"weights_atomic_{uuid.uuid4().hex[:8]}@example.com"
    payload = {
        "email": email,
        "password": "StrongPass123",
        "preferred_name": "Test",
        "last_name": "User",
        "onboarding": {
            "fitnessGoal": "strength",
            "dumbbellWeights": [10],
            "plateWeights": [5],
        },
    }

    def fail_weight_seed(*args, **kwargs):
        raise RuntimeError("weight seed failed")

    monkeypatch.setattr(
        "app.api.v1.auth.router.upsert_equipment_weights", fail_weight_seed
    )
    with pytest.raises(RuntimeError, match="weight seed failed"):
        client.post("/v1/auth/signup", json=payload)

    monkeypatch.undo()
    retry = client.post("/v1/auth/signup", json=payload)

    assert retry.status_code == 201
