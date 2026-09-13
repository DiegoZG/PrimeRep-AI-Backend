import asyncio
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.core.email_service import (
    CaptureEmailSender,
    EmailMessage,
    ResendEmailSender,
    deliver_email,
    get_email_sender,
)
from app.core.response_timing import (
    MinimumResponseBudget,
    get_password_reset_response_budget,
)
from app.core.security.passwords import hash_password
from app.core.security.jwt import create_access_token
from app.core.settings import settings
from app.main import app
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from conftest import LEGAL_ACCEPTANCE


client = TestClient(app)


@pytest.fixture
def response_budget():
    class ImmediateResponseBudget:
        def __init__(self):
            self.started = 0
            self.waited = []

        def start(self):
            self.started += 1
            return float(self.started)

        async def wait(self, started_at):
            self.waited.append(started_at)

    budget = ImmediateResponseBudget()
    app.dependency_overrides[get_password_reset_response_budget] = lambda: budget
    yield budget
    app.dependency_overrides.pop(get_password_reset_response_budget, None)


@pytest.fixture
def capture_sender(monkeypatch, response_budget):
    sender = CaptureEmailSender()
    app.dependency_overrides[get_email_sender] = lambda: sender
    monkeypatch.setattr(settings, "PASSWORD_RESET_URL_BASE", "https://app.primerep.test/reset-password")
    yield sender
    app.dependency_overrides.pop(get_email_sender, None)


def _signup(prefix: str = "reset") -> dict[str, str]:
    email = f"{prefix}_{uuid.uuid4().hex}@example.com"
    response = client.post(
        "/v1/auth/signup",
        json={
            "email": email,
            "password": "StrongPass123",
            "preferred_name": "Reset",
            "legalAcceptance": LEGAL_ACCEPTANCE,
        },
    )
    assert response.status_code == 201
    return {**response.json(), "email": email}


def _request(email: str):
    return client.post("/v1/auth/password-reset/request", json={"email": email})


def _raw_token(message: EmailMessage) -> str:
    marker = 'href="'
    url = message.html.split(marker, 1)[1].split('"', 1)[0]
    return parse_qs(urlparse(url).query)["token"][0]


def test_request_is_generic_hashes_token_and_enforces_cooldown(capture_sender):
    tokens = _signup("generic")

    existing = _request(tokens["email"])
    missing = _request(f"missing_{uuid.uuid4().hex}@example.com")
    repeated = _request(tokens["email"].upper())

    assert existing.status_code == 202
    assert missing.status_code == 202
    assert repeated.status_code == 202
    assert existing.json() == missing.json() == repeated.json()
    assert len(capture_sender.messages) == 1

    raw_token = _raw_token(capture_sender.messages[0])
    with SessionLocal() as db:
        stored = db.query(PasswordResetToken).filter_by(token_hash=hashlib.sha256(raw_token.encode()).hexdigest()).one()
        assert stored.used_at is None
        assert raw_token not in stored.token_hash


def test_public_request_waits_on_the_same_budget_for_known_and_unknown_emails(
    capture_sender,
    response_budget,
):
    tokens = _signup("timing")

    assert _request(tokens["email"]).status_code == 202
    assert _request(f"missing_{uuid.uuid4().hex}@example.com").status_code == 202

    assert response_budget.started == 2
    assert response_budget.waited == [1.0, 2.0]


def test_minimum_response_budget_uses_nonblocking_remaining_delay():
    times = iter([10.0, 10.1])
    delays = []

    async def record_sleep(delay):
        delays.append(delay)

    budget = MinimumResponseBudget(
        minimum_seconds=0.35,
        clock=lambda: next(times),
        sleeper=record_sleep,
    )
    started_at = budget.start()
    asyncio.run(budget.wait(started_at))

    assert delays == pytest.approx([0.25])


def test_signup_login_and_reset_share_case_insensitive_email_identity(capture_sender):
    email = f"Mixed.Case_{uuid.uuid4().hex}@Example.COM"
    first = client.post(
        "/v1/auth/signup",
        json={
            "email": email,
            "password": "StrongPass123",
            "preferred_name": "Mixed",
            "legalAcceptance": LEGAL_ACCEPTANCE,
        },
    )
    duplicate = client.post(
        "/v1/auth/signup",
        json={
            "email": email.lower(),
            "password": "StrongPass123",
            "preferred_name": "Duplicate",
            "legalAcceptance": LEGAL_ACCEPTANCE,
        },
    )
    login = client.post(
        "/v1/auth/login",
        json={"email": email.swapcase(), "password": "StrongPass123"},
    )
    reset = _request(email.swapcase())

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert login.status_code == 200
    assert reset.status_code == 202
    assert len(capture_sender.messages) == 1
    assert capture_sender.messages[0].recipient == email.lower()


def test_database_rejects_case_insensitive_email_duplicates():
    email = f"Db.Case_{uuid.uuid4().hex}@example.com"
    with SessionLocal() as db:
        db.add_all(
            [
                User(
                    email=email,
                    password_hash=hash_password("StrongPass123"),
                    preferred_name="First",
                ),
                User(
                    email=email.lower(),
                    password_hash=hash_password("StrongPass123"),
                    preferred_name="Second",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_new_request_invalidates_the_previous_link(capture_sender):
    tokens = _signup("newest")
    assert _request(tokens["email"]).status_code == 202
    first_token = _raw_token(capture_sender.messages[-1])

    with SessionLocal() as db:
        reset = db.query(PasswordResetToken).filter_by(token_hash=hashlib.sha256(first_token.encode()).hexdigest()).one()
        reset.created_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        db.commit()

    assert _request(tokens["email"]).status_code == 202
    second_token = _raw_token(capture_sender.messages[-1])
    assert second_token != first_token
    assert client.post(
        "/v1/auth/password-reset/confirm",
        json={"token": first_token, "newPassword": "Replacement123"},
    ).status_code == 400


def test_confirm_changes_password_is_single_use_and_invalidates_sessions(capture_sender):
    tokens = _signup("confirm")
    user_id = client.get(
        "/v1/users/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    ).json()["id"]
    legacy_access = create_access_token(user_id)

    assert _request(tokens["email"]).status_code == 202
    raw_token = _raw_token(capture_sender.messages[-1])
    confirmed = client.post(
        "/v1/auth/password-reset/confirm",
        json={"token": raw_token, "newPassword": "Replacement123"},
    )

    assert confirmed.status_code == 204
    assert len(capture_sender.messages) == 2
    assert capture_sender.messages[-1].idempotency_key.startswith("password-changed/")
    assert client.post(
        "/v1/auth/password-reset/confirm",
        json={"token": raw_token, "newPassword": "AnotherPass123"},
    ).status_code == 400
    assert client.get(
        "/v1/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    ).status_code == 401
    assert client.get(
        "/v1/users/me", headers={"Authorization": f"Bearer {legacy_access}"}
    ).status_code == 401
    assert client.post(
        "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    ).status_code == 401
    assert client.post(
        "/v1/auth/login",
        json={"email": tokens["email"], "password": "StrongPass123"},
    ).status_code == 401
    assert client.post(
        "/v1/auth/login",
        json={"email": tokens["email"], "password": "Replacement123"},
    ).status_code == 200


def test_expired_token_is_rejected_without_changing_password(capture_sender):
    tokens = _signup("expired")
    assert _request(tokens["email"]).status_code == 202
    raw_token = _raw_token(capture_sender.messages[-1])
    with SessionLocal() as db:
        reset = db.query(PasswordResetToken).filter_by(token_hash=hashlib.sha256(raw_token.encode()).hexdigest()).one()
        reset.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    response = client.post(
        "/v1/auth/password-reset/confirm",
        json={"token": raw_token, "newPassword": "Replacement123"},
    )
    assert response.status_code == 400
    assert client.post(
        "/v1/auth/login",
        json={"email": tokens["email"], "password": "StrongPass123"},
    ).status_code == 200


def test_concurrent_confirmation_allows_one_success(capture_sender):
    tokens = _signup("concurrent")
    assert _request(tokens["email"]).status_code == 202
    raw_token = _raw_token(capture_sender.messages[-1])

    def confirm():
        return client.post(
            "/v1/auth/password-reset/confirm",
            json={"token": raw_token, "newPassword": "Replacement123"},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _: confirm(), range(2)))
    assert statuses == [204, 400]


def test_concurrent_request_and_confirmation_do_not_deadlock(capture_sender):
    tokens = _signup("request_confirm")
    assert _request(tokens["email"]).status_code == 202
    raw_token = _raw_token(capture_sender.messages[-1])
    with SessionLocal() as db:
        reset = db.query(PasswordResetToken).filter_by(
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest()
        ).one()
        reset.created_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        db.commit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        request_future = executor.submit(_request, tokens["email"])
        confirm_future = executor.submit(
            client.post,
            "/v1/auth/password-reset/confirm",
            json={"token": raw_token, "newPassword": "Replacement123"},
        )
        request_status = request_future.result(timeout=5).status_code
        confirm_status = confirm_future.result(timeout=5).status_code

    assert request_status == 202
    assert confirm_status in {204, 400}


def test_issuance_deletes_only_a_bounded_batch_of_old_tokens(capture_sender):
    tokens = _signup("cleanup")
    with SessionLocal() as db:
        user = db.query(User).filter_by(email=tokens["email"]).one()
        user_id = user.id
        old = datetime.now(timezone.utc) - timedelta(days=31)
        db.add_all(
            [
                PasswordResetToken(
                    user_id=user_id,
                    token_hash=hashlib.sha256(
                        f"{user_id}-stale-{number}".encode()
                    ).hexdigest(),
                    expires_at=old,
                    used_at=old,
                )
                for number in range(105)
            ]
        )
        db.commit()

    assert _request(tokens["email"]).status_code == 202
    with SessionLocal() as db:
        remaining = db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.expires_at < datetime.now(timezone.utc) - timedelta(days=30),
        ).count()
    assert remaining == 5


def test_password_reset_rate_limits_are_ip_scoped(capture_sender):
    for _ in range(3):
        assert _request(f"missing_{uuid.uuid4().hex}@example.com").status_code == 202
    assert _request(f"missing_{uuid.uuid4().hex}@example.com").status_code == 429


def test_password_reset_confirmation_limit_is_ip_scoped(capture_sender):
    for _ in range(5):
        response = client.post(
            "/v1/auth/password-reset/confirm",
            json={"token": f"invalid-{uuid.uuid4().hex}", "newPassword": "Replacement123"},
        )
        assert response.status_code == 400
    assert client.post(
        "/v1/auth/password-reset/confirm",
        json={"token": f"invalid-{uuid.uuid4().hex}", "newPassword": "Replacement123"},
    ).status_code == 429


def test_account_deletion_cascades_reset_tokens(capture_sender):
    tokens = _signup("cascade")
    assert _request(tokens["email"]).status_code == 202
    raw_token = _raw_token(capture_sender.messages[-1])
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    assert client.delete(
        "/v1/users/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    ).status_code == 204
    with SessionLocal() as db:
        assert db.query(PasswordResetToken).filter_by(token_hash=token_hash).first() is None


def test_resend_adapter_uses_documented_idempotency_option():
    calls = []
    fake_module = SimpleNamespace(
        api_key=None,
        Emails=SimpleNamespace(send=lambda params, options: calls.append((params, options))),
    )
    sender = ResendEmailSender("re_test", "PrimeRep <test@example.com>", fake_module)
    sender.send(EmailMessage("user@example.com", "Subject", "<p>Body</p>", "password-reset/id"))

    assert fake_module.api_key == "re_test"
    assert calls == [
        (
            {
                "from": "PrimeRep <test@example.com>",
                "to": ["user@example.com"],
                "subject": "Subject",
                "html": "<p>Body</p>",
            },
            {"idempotency_key": "password-reset/id"},
        )
    ]


def test_delivery_failure_is_caught_without_exposing_message(caplog):
    class FailingSender:
        def send(self, message):
            raise RuntimeError("provider unavailable")

    message = EmailMessage(
        "private@example.com",
        "Subject",
        "<p>secret-token</p>",
        "password-reset/id",
    )
    deliver_email(FailingSender(), message)

    assert "Email delivery failed" in caplog.text
    assert "private@example.com" not in caplog.text
    assert "secret-token" not in caplog.text


@pytest.mark.parametrize(
    "acceptance",
    [
        None,
        {"accepted": 1, "termsVersion": "2026-09-12", "privacyVersion": "2026-09-12"},
        {"accepted": False, "termsVersion": "2026-09-12", "privacyVersion": "2026-09-12"},
        {"accepted": True, "termsVersion": "old", "privacyVersion": "2026-09-12"},
        {"accepted": True, "termsVersion": "2026-09-12", "privacyVersion": "old"},
    ],
)
def test_signup_requires_current_legal_acceptance(acceptance):
    payload = {
        "email": f"legal_{uuid.uuid4().hex}@example.com",
        "password": "StrongPass123",
        "preferred_name": "Legal",
    }
    if acceptance is not None:
        payload["legalAcceptance"] = acceptance
    assert client.post("/v1/auth/signup", json=payload).status_code == 422


def test_signup_records_server_timestamp_and_legal_versions():
    tokens = _signup("legal_saved")
    with SessionLocal() as db:
        user = db.query(User).filter_by(email=tokens["email"]).one()
        assert user.terms_accepted_version == "2026-09-12"
        assert user.privacy_accepted_version == "2026-09-12"
        assert user.legal_accepted_at is not None
