import uuid

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.push_token import PushToken


client = TestClient(app)


def _signup(prefix: str):
    response = client.post(
        "/v1/auth/signup",
        json={
            "email": f"push_{prefix}_{uuid.uuid4().hex[:8]}@example.com",
            "password": "StrongPass123",
            "preferred_name": "Push",
            "last_name": "Test",
        },
    )
    assert response.status_code == 201
    return response.json()


def _auth(token: str):
    return {"Authorization": f"Bearer {token}"}


def test_push_token_requires_authentication():
    response = client.post(
        "/v1/users/me/push-token",
        json={"token": "ExponentPushToken[auth]", "platform": "ios"},
    )
    assert response.status_code == 401

    delete_response = client.request(
        "DELETE",
        "/v1/users/me/push-token",
        json={"token": "ExponentPushToken[auth]"},
    )
    assert delete_response.status_code == 401


def test_push_token_validates_token_and_platform():
    tokens = _signup("validation")
    headers = _auth(tokens["access_token"])

    blank = client.post(
        "/v1/users/me/push-token",
        headers=headers,
        json={"token": "  ", "platform": "ios"},
    )
    invalid_platform = client.post(
        "/v1/users/me/push-token",
        headers=headers,
        json={"token": "ExponentPushToken[valid]", "platform": "web"},
    )

    assert blank.status_code == 422
    assert invalid_platform.status_code == 422


def test_push_token_registration_is_idempotent_and_echoes_payload():
    tokens = _signup("idempotent")
    headers = _auth(tokens["access_token"])
    payload = {"token": "ExponentPushToken[idempotent]", "platform": "android"}

    first = client.post("/v1/users/me/push-token", headers=headers, json=payload)
    second = client.post("/v1/users/me/push-token", headers=headers, json=payload)

    assert first.status_code == 200
    assert first.json() == payload
    assert second.json() == payload
    with SessionLocal() as db:
        assert db.query(PushToken).filter_by(token=payload["token"]).count() == 1


def test_user_can_register_multiple_device_tokens():
    tokens = _signup("multiple")
    headers = _auth(tokens["access_token"])

    for platform in ("ios", "android"):
        response = client.post(
            "/v1/users/me/push-token",
            headers=headers,
            json={"token": f"ExponentPushToken[{platform}-device]", "platform": platform},
        )
        assert response.status_code == 200

    user_id = client.get("/v1/users/me", headers=headers).json()["id"]
    with SessionLocal() as db:
        assert db.query(PushToken).filter_by(user_id=user_id).count() == 2


def test_registering_existing_token_transfers_ownership():
    first = _signup("first_owner")
    second = _signup("second_owner")
    token = "ExponentPushToken[transferred-device]"

    client.post(
        "/v1/users/me/push-token",
        headers=_auth(first["access_token"]),
        json={"token": token, "platform": "ios"},
    )
    response = client.post(
        "/v1/users/me/push-token",
        headers=_auth(second["access_token"]),
        json={"token": token, "platform": "android"},
    )

    assert response.status_code == 200
    second_user_id = client.get(
        "/v1/users/me", headers=_auth(second["access_token"])
    ).json()["id"]
    with SessionLocal() as db:
        stored = db.get(PushToken, token)
        assert stored.user_id == second_user_id
        assert stored.platform == "android"


def test_unregister_removes_only_current_users_token_and_is_idempotent():
    first = _signup("unregister_owner")
    second = _signup("unregister_other")
    owned_token = "ExponentPushToken[owned-device]"
    other_token = "ExponentPushToken[other-device]"

    client.post(
        "/v1/users/me/push-token",
        headers=_auth(first["access_token"]),
        json={"token": owned_token, "platform": "ios"},
    )
    client.post(
        "/v1/users/me/push-token",
        headers=_auth(second["access_token"]),
        json={"token": other_token, "platform": "android"},
    )

    not_owned = client.request(
        "DELETE",
        "/v1/users/me/push-token",
        headers=_auth(first["access_token"]),
        json={"token": other_token},
    )
    first_delete = client.request(
        "DELETE",
        "/v1/users/me/push-token",
        headers=_auth(first["access_token"]),
        json={"token": owned_token},
    )
    second_delete = client.request(
        "DELETE",
        "/v1/users/me/push-token",
        headers=_auth(first["access_token"]),
        json={"token": owned_token},
    )

    assert not_owned.status_code == 204
    assert first_delete.status_code == 204
    assert second_delete.status_code == 204
    with SessionLocal() as db:
        assert db.get(PushToken, owned_token) is None
        assert db.get(PushToken, other_token) is not None


def test_account_deletion_cascades_push_tokens():
    tokens = _signup("cascade")
    headers = _auth(tokens["access_token"])
    token = "ExponentPushToken[delete-with-user]"
    client.post(
        "/v1/users/me/push-token",
        headers=headers,
        json={"token": token, "platform": "ios"},
    )

    assert client.delete("/v1/users/me", headers=headers).status_code == 204
    with SessionLocal() as db:
        assert db.get(PushToken, token) is None
