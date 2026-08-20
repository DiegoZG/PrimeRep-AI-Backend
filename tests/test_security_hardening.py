import uuid

from fastapi.testclient import TestClient
from jose import jwt

from app.api.v1.workouts import router as workouts_router
from app.core import exercise_qa_service
from app.core.rate_limit import reset_rate_limits
from app.core.security.jwt import decode_refresh_token
from app.core.settings import settings
from app.main import app


client = TestClient(app)


def _signup(prefix="security", onboarding=None):
    email = f"{prefix}_{uuid.uuid4().hex}@example.com"
    response = client.post(
        "/v1/auth/signup",
        json={
            "email": email,
            "password": "StrongPass123",
            "preferred_name": "Security",
            "onboarding": onboarding,
        },
    )
    assert response.status_code == 201
    data = response.json()
    data["email"] = email
    return data


def test_signup_limit_is_ip_scoped():
    for _ in range(3):
        assert _signup().get("access_token")
    response = client.post(
        "/v1/auth/signup",
        json={"email": f"limited_{uuid.uuid4().hex}@example.com", "password": "StrongPass123", "preferred_name": "Limited"},
    )
    assert response.status_code == 429


def test_login_limit_is_ip_scoped():
    for _ in range(5):
        response = client.post(
            "/v1/auth/login",
            json={"email": f"missing_{uuid.uuid4().hex}@example.com", "password": "StrongPass123"},
        )
        assert response.status_code == 401
    response = client.post(
        "/v1/auth/login",
        json={"email": f"limited_{uuid.uuid4().hex}@example.com", "password": "StrongPass123"},
    )
    assert response.status_code == 429


def test_refresh_rotates_and_replay_is_rejected():
    tokens = _signup()
    rotated = client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != tokens["refresh_token"]
    assert client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.post("/v1/auth/refresh", json={"refresh_token": rotated.json()["refresh_token"]}).status_code == 200


def test_legacy_refresh_without_jti_is_rejected():
    tokens = _signup()
    payload = decode_refresh_token(tokens["refresh_token"])
    payload.pop("jti")
    legacy = jwt.encode(
        payload,
        settings.JWT_REFRESH_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )
    response = client.post("/v1/auth/refresh", json={"refresh_token": legacy})
    assert response.status_code == 401


def test_logout_revokes_one_refresh_token():
    first = _signup("first")
    second = _signup("second")
    assert client.post("/v1/auth/logout", json={"refresh_token": first["refresh_token"]}).status_code == 204
    assert client.post("/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401
    assert client.post("/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 200


def test_logout_revokes_only_one_of_same_users_refresh_tokens():
    tokens = _signup("same_user")
    login = client.post(
        "/v1/auth/login",
        json={"email": tokens["email"], "password": "StrongPass123"},
    )
    assert login.status_code == 200
    second_refresh = login.json()["refresh_token"]

    assert client.post("/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204
    assert client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.post("/v1/auth/refresh", json={"refresh_token": second_refresh}).status_code == 200


def test_logout_is_idempotent_and_rejects_malformed_tokens():
    tokens = _signup()
    assert client.post("/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204
    assert client.post("/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204
    assert client.post("/v1/auth/logout", json={"refresh_token": "invalid"}).status_code == 401


def test_onboarding_types_and_unknown_fields_are_preserved():
    tokens = _signup("typed")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    response = client.post(
        "/v1/onboarding/me",
        headers=headers,
        json={"data": {"age": 31, "futureField": "preserve-me"}},
    )
    assert response.status_code == 200
    assert response.json()["data"]["futureField"] == "preserve-me"
    invalid = client.post("/v1/onboarding/me", headers=headers, json={"data": {"age": "not-a-number"}})
    assert invalid.status_code == 422


def test_signup_preserves_unknown_onboarding_fields():
    tokens = _signup("signup_extra", onboarding={"futureField": "preserve-me"})
    response = client.get(
        "/v1/onboarding/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["futureField"] == "preserve-me"


def test_onboarding_rejects_malformed_weight_arrays_for_save_and_signup():
    tokens = _signup("weights")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    for field in ("dumbbellWeights", "plateWeights"):
        malformed = client.post(
            "/v1/onboarding/me",
            headers=headers,
            json={"data": {field: [5, "bad"]}},
        )
        assert malformed.status_code == 422
        signup = client.post(
            "/v1/auth/signup",
            json={
                "email": f"bad_{field}_{uuid.uuid4().hex}@example.com",
                "password": "StrongPass123",
                "preferred_name": "Bad",
                "onboarding": {field: [5, "bad"]},
            },
        )
        assert signup.status_code == 422


def test_onboarding_accepts_explicit_null_weight_arrays():
    tokens = _signup("null_weights")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    response = client.post(
        "/v1/onboarding/me",
        headers=headers,
        json={"data": {"dumbbellWeights": None, "plateWeights": None}},
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"dumbbellWeights": None, "plateWeights": None}


def test_cors_allows_only_configured_local_origin():
    allowed = client.options(
        "/v1/auth/login",
        headers={"Origin": "http://localhost:8081", "Access-Control-Request-Method": "POST"},
    )
    denied = client.options(
        "/v1/auth/login",
        headers={"Origin": "https://not-allowed.example", "Access-Control-Request-Method": "POST"},
    )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:8081"
    assert "access-control-allow-origin" not in denied.headers


def test_cors_allows_configured_simple_requests():
    response = client.get("/health", headers={"Origin": "http://localhost:8081"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:8081"


def test_limiter_reset_clears_ip_scoped_signup_state():
    for _ in range(3):
        assert _signup("reset").get("access_token")
    reset_rate_limits()
    assert _signup("reset_after").get("access_token")


def test_qa_limit_is_user_scoped(monkeypatch):
    monkeypatch.setattr(
        exercise_qa_service,
        "_call_anthropic",
        lambda system, question: "A mocked answer.",
    )
    first = _signup("qa_first")
    second = _signup("qa_second")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}

    for number in range(5):
        response = client.post(
            "/v1/exercises/push_up/ask",
            headers=first_headers,
            json={"question": f"Question {number}?"},
        )
        assert response.status_code == 200

    assert client.post(
        "/v1/exercises/push_up/ask",
        headers=first_headers,
        json={"question": "One too many?"},
    ).status_code == 429
    assert client.post(
        "/v1/exercises/push_up/ask",
        headers=second_headers,
        json={"question": "A separate user is allowed?"},
    ).status_code == 200


def test_force_regeneration_limit_is_shared_and_non_force_bypasses(monkeypatch):
    tokens = _signup("force")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    monkeypatch.setattr(workouts_router, "_check_and_increment_force_regen", lambda db, user_id: None)

    assert client.get("/v1/workouts/week", headers=headers).status_code == 200
    assert client.get("/v1/workouts/week?force=true", headers=headers).status_code == 200
    assert client.post("/v1/workouts/next?force=true", headers=headers).status_code == 200
    assert client.get("/v1/workouts/week?force=true", headers=headers).status_code == 200
    assert client.post("/v1/workouts/next?force=true", headers=headers).status_code == 429
