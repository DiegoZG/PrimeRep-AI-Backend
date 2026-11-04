import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.api.v1.billing import router
from app.core import revenuecat_client, subscription_service as service
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.core.security.deps import get_current_user
from app.core.settings import settings
from app.core.security.jwt import create_access_token, decode_access_token
from app.models.feature_flags import FeatureFlagAudit, FeatureFlagConfig, FeatureFlagUserOverride
from app.models.subscription import BillingJob, BillingWebhookEvent, SubscriptionState, SubscriptionTransitionGrant
from app.models.user import User

A = "00000000-0000-4000-8000-000000000011"
B = "00000000-0000-4000-8000-000000000012"
NOW = datetime(2026, 11, 1, 15, tzinfo=timezone.utc)
PRODUCT = "primerep.monthly"


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'billing.db'}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    for model in (User, FeatureFlagConfig, FeatureFlagAudit, FeatureFlagUserOverride, SubscriptionState,
                  SubscriptionTransitionGrant, BillingWebhookEvent, BillingJob):
        model.__table__.create(engine)
    monkeypatch.setattr(settings, "APP_ENV", "test")
    monkeypatch.setattr(settings, "REVENUECAT_SECRET_API_KEY", "server-test-key")
    monkeypatch.setattr(settings, "REVENUECAT_PRODUCT_IDS", frozenset({PRODUCT}))
    monkeypatch.setattr(settings, "REVENUECAT_APP_IDS", frozenset({"app-test"}))
    monkeypatch.setattr(settings, "REVENUECAT_WEBHOOK_AUTHORIZATION", "Bearer webhook-test")
    monkeypatch.setattr(settings, "REVENUECAT_WEBHOOK_SIGNING_SECRET", "")
    monkeypatch.setattr(settings, "REVENUECAT_PRODUCTION_TEST_USER_IDS", frozenset())
    monkeypatch.setattr(service, "_now", lambda: NOW)
    monkeypatch.setattr(revenuecat_client, "fetch_customer", lambda _: {"subscriber": {"entitlements": {}}})
    with Session(engine) as db:
        for user_id in (A, B):
            db.add(User(id=user_id, email=f"{user_id}@example.test", preferred_name="Test", password_hash="test",
                        created_at=NOW - timedelta(days=100)))
        db.commit()
        yield db
    engine.dispose()


def enforce(db, *, launch=None, environment="test"):
    db.add(FeatureFlagConfig(environment=environment, version=1, subscriptions_purchases_enabled=True,
                            subscriptions_enforcement_enabled=True, subscriptions_launch_at=launch or NOW - timedelta(days=31),
                            launch_locked=True, updated_at=NOW))
    db.commit()


def customer(*, expiry=None, grace=None, period="normal", sandbox=False, cancelled=False, refund=None, product=PRODUCT, request_at=NOW):
    expiry = expiry or NOW + timedelta(days=20)
    return {"request_date": request_at.isoformat(), "subscriber": {
        "entitlements": {"premium": {"product_identifier": product, "expires_date": expiry.isoformat(),
                                      "grace_period_expires_date": grace.isoformat() if grace else None}},
        "subscriptions": {product: {"is_sandbox": sandbox, "period_type": period,
                                    "purchase_date": (NOW - timedelta(days=10)).isoformat(),
                                    "refunded_at": refund.isoformat() if refund else None,
                                    "unsubscribe_detected_at": NOW.isoformat() if cancelled else None}},
        "management_url": "https://apps.apple.com/account/subscriptions",
    }}


def verify(db, monkeypatch, value, user_id=A):
    monkeypatch.setattr(revenuecat_client, "fetch_customer", lambda _: value)
    result = service.reconcile_subscription(db, user_id, now=NOW)
    db.commit()
    return result


def test_default_off_preserves_baseline_without_faking_premium(db, monkeypatch):
    monkeypatch.setattr(settings, "REVENUECAT_SECRET_API_KEY", "")
    result = service.get_subscription(db, A)
    assert result["can_start_training"] is True
    assert result["access_source"] == "legacy"
    assert result["has_premium"] is False
    assert result["verification_status"] == "not_configured"
    assert service.require_training_access(db, A)["can_start_training"] is True
    assert db.scalar(select(func.count()).select_from(SubscriptionTransitionGrant)) == 0


def onboarding(db, user_id=A):
    enforce(db)
    config = db.get(FeatureFlagConfig, "test")
    config.subscriptions_onboarding_enabled = True
    db.get(User, user_id).has_completed_onboarding = True
    db.get(User, user_id).created_at = NOW
    db.commit()
    service.enroll_subscription_onboarding(db, user_id)
    db.commit()


def test_onboarding_enrollment_requires_all_flags_and_excludes_existing_accounts(db):
    service.enroll_subscription_onboarding(db, A)
    assert db.get(SubscriptionState, ("test", A)) is None
    onboarding(db)
    assert service.get_subscription(db, A)["onboarding_subscription_required"] is True
    assert service.get_subscription(db, B)["onboarding_subscription_required"] is False
    override = FeatureFlagUserOverride(environment="test", user_id=A)
    db.add(override)
    for flag in ("subscriptions_purchases_enabled", "subscriptions_enforcement_enabled", "subscriptions_onboarding_enabled"):
        setattr(override, flag, False)
        db.flush()
        assert service.get_subscription(db, A)["onboarding_subscription_required"] is False
        setattr(override, flag, None)
    db.delete(override)
    db.flush()
    assert service.get_subscription(db, A)["onboarding_subscription_required"] is True


def test_onboarding_targeting_unknown_verification_and_environment_isolation(db, monkeypatch):
    onboarding(db)
    db.get(FeatureFlagConfig, "test").subscriptions_onboarding_audience = "selected"
    db.commit()
    assert service.get_subscription(db, A)["onboarding_subscription_required"] is False
    db.add(FeatureFlagUserOverride(environment="test", user_id=A, subscriptions_onboarding_enabled=True))
    db.commit()
    assert service.get_subscription(db, A)["onboarding_subscription_required"] is True
    assert service.get_subscription(db, A)["can_start_training"] is None
    state = db.get(SubscriptionState, ("test", A))
    state.verification_status = "unavailable"
    db.commit()
    assert service.get_subscription(db, A)["onboarding_subscription_required"] is True
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert service.get_subscription(db, A)["onboarding_subscription_required"] is False


@pytest.mark.parametrize("period", ["normal", "trial"])
def test_onboarding_completion_persists_across_sessions_and_expiry(db, monkeypatch, period):
    onboarding(db)
    assert verify(db, monkeypatch, customer(period=period))["onboarding_subscription_required"] is False
    with Session(db.bind) as reopened:
        assert reopened.get(SubscriptionState, ("test", A)).onboarding_completed_at is not None
        assert service.get_subscription(reopened, A, now=NOW + timedelta(days=50))["onboarding_subscription_required"] is False


def test_valid_transition_permanently_completes_onboarding_requirement(db):
    onboarding(db)
    db.get(User, A).created_at = NOW - timedelta(days=10)
    db.get(FeatureFlagConfig, "test").subscriptions_launch_at = NOW - timedelta(days=5)
    db.commit()
    assert service.get_subscription(db, A)["onboarding_subscription_required"] is False
    db.commit()
    with Session(db.bind) as reopened:
        assert reopened.get(SubscriptionState, ("test", A)).onboarding_completed_at is not None
        assert service.get_subscription(reopened, A, now=NOW + timedelta(days=40))["onboarding_subscription_required"] is False


@pytest.mark.parametrize("enabled", [False, True])
def test_signup_enrollment_and_authenticated_bootstrap_hint(db, monkeypatch, enabled):
    from app.api.v1.auth import router as auth_module
    from app.api.v1.users.router import router as users_router
    from app.core.legal import TERMS_VERSION, PRIVACY_VERSION

    enforce(db)
    db.get(FeatureFlagConfig, "test").subscriptions_onboarding_enabled = enabled
    db.commit()
    owner = "00000000-0000-4000-8000-000000000013"

    def create_signup_user(session, **values):
        values.pop("commit")
        user = User(id=owner, created_at=NOW, **values)
        session.add(user)
        session.flush()
        return user

    monkeypatch.setattr(auth_module, "create_user", create_signup_user)
    monkeypatch.setattr(auth_module, "hash_password", lambda _: "test")
    monkeypatch.setattr(auth_module, "upsert_onboarding", lambda *args, **kwargs: None)
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(auth_module.router, prefix="/v1")
    app.include_router(users_router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: db.get(User, owner)
    with TestClient(app) as client:
        response = client.post("/v1/auth/signup", json={"email": "new@example.com", "password": "test-password", "preferred_name": "New", "onboarding": {}, "legalAcceptance": {"accepted": True, "termsVersion": TERMS_VERSION, "privacyVersion": PRIVACY_VERSION}})
        assert response.status_code == 201
        assert client.get("/v1/users/me").json()["onboarding_subscription_required"] is enabled
    with Session(db.bind) as reopened:
        state = reopened.get(SubscriptionState, ("test", owner))
        assert bool(state and state.onboarding_enrolled_at) is enabled


@pytest.mark.parametrize("period,status", [("normal", "active"), ("trial", "trial")])
def test_verified_subscription_authorizes_and_projects_tier(db, monkeypatch, period, status):
    enforce(db)
    result = verify(db, monkeypatch, customer(period=period))
    assert result["billing_status"] == status
    assert result["has_premium"] and result["can_start_training"]
    assert result["access_source"] == "subscription"
    assert db.get(User, A).subscription_tier == "premium"


def test_cancellation_preserves_access_and_grace_is_honored(db, monkeypatch):
    enforce(db)
    result = verify(db, monkeypatch, customer(cancelled=True))
    assert result["has_premium"] and result["will_renew"] is False
    result = verify(db, monkeypatch, customer(expiry=NOW - timedelta(hours=1), grace=NOW + timedelta(days=2)))
    assert result["billing_status"] == "grace" and result["has_premium"]


@pytest.mark.parametrize("value", [customer(expiry=NOW), customer(refund=NOW - timedelta(hours=1)), {"subscriber": {"entitlements": {}}}])
def test_confirmed_expiry_refund_or_absence_denies_new_training(db, monkeypatch, value):
    enforce(db)
    result = verify(db, monkeypatch, value)
    assert result["can_start_training"] is False
    assert result["has_premium"] is False
    with pytest.raises(service.SubscriptionAccessError) as error:
        service.require_training_access(db, A)
    assert error.value.status_code == 403 and error.value.code == "subscription_required"


def test_transition_uses_locked_global_date_and_account_creation(db):
    enforce(db, launch=NOW - timedelta(days=5))
    snapshot = service.get_subscription(db, A)
    assert snapshot["access_source"] == "transition" and not snapshot["has_premium"]
    assert snapshot["access_expires_at"] == NOW + timedelta(days=25)
    db.get(User, B).created_at = NOW - timedelta(days=5)
    db.commit()
    assert service.get_subscription(db, B)["can_start_training"] is None
    assert service.get_subscription(db, A, now=NOW + timedelta(days=25))["can_start_training"] is None
    assert db.scalar(select(func.count()).select_from(SubscriptionTransitionGrant)) == 1


def test_provisional_transition_does_not_create_grants(db):
    db.add(FeatureFlagConfig(environment="test", version=1, subscriptions_purchases_enabled=True,
                            subscriptions_enforcement_enabled=False, subscriptions_launch_at=NOW - timedelta(days=5),
                            launch_locked=False, updated_at=NOW))
    db.commit()
    assert service.get_subscription(db, A)["transition_ends_at"] is None
    assert db.scalar(select(func.count()).select_from(SubscriptionTransitionGrant)) == 0


def test_manual_premium_is_not_paid_proof(db):
    enforce(db)
    db.get(User, A).subscription_tier = "premium"
    db.commit()
    snapshot = service.get_subscription(db, A)
    assert not snapshot["has_premium"] and snapshot["can_start_training"] is None


def test_outage_retains_verified_access_until_deadline_then_is_unknown(db, monkeypatch):
    enforce(db)
    verify(db, monkeypatch, customer(expiry=NOW + timedelta(minutes=1)))

    def unavailable(_):
        raise revenuecat_client.RevenueCatUnavailable("unavailable")

    monkeypatch.setattr(revenuecat_client, "fetch_customer", unavailable)
    with pytest.raises(service.SubscriptionAccessError):
        service.reconcile_subscription(db, A)
    db.commit()
    assert service.get_subscription(db, A)["can_start_training"] is True
    result = service.get_subscription(db, A, now=NOW + timedelta(minutes=1))
    assert result["can_start_training"] is None and not result["has_premium"]
    monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(minutes=1))
    with pytest.raises(service.SubscriptionAccessError) as error:
        service.require_training_access(db, A)
    assert error.value.code == "subscription_verification_unavailable"


def test_unconfigured_enforced_account_is_retryable_not_paywall(db, monkeypatch):
    enforce(db)
    monkeypatch.setattr(settings, "REVENUECAT_SECRET_API_KEY", "")
    with pytest.raises(service.SubscriptionAccessError) as error:
        service.require_training_access(db, A)
    assert error.value.status_code == 503
    assert service.get_subscription(db, A)["verification_status"] == "not_configured"


def test_product_and_sandbox_are_classified_per_transaction(db, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    enforce(db, environment="production")
    assert verify(db, monkeypatch, customer(sandbox=True))["has_premium"] is False
    monkeypatch.setattr(settings, "REVENUECAT_PRODUCTION_TEST_USER_IDS", frozenset({A}))
    assert verify(db, monkeypatch, customer(sandbox=True))["has_premium"] is True
    assert verify(db, monkeypatch, customer(product="other-project-product"))["has_premium"] is False


def test_invalid_subscription_expiry_never_grants_unlimited_access(db, monkeypatch):
    enforce(db)
    value = customer()
    value["subscriber"]["entitlements"]["premium"]["expires_date"] = None
    monkeypatch.setattr(revenuecat_client, "fetch_customer", lambda _: value)
    with pytest.raises(service.SubscriptionAccessError):
        service.reconcile_subscription(db, A)
    assert service.get_subscription(db, A)["can_start_training"] is None


def test_mixed_sandbox_and_production_products_preserve_verified_paid_access(db, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "REVENUECAT_PRODUCT_IDS", frozenset({PRODUCT, "primerep.annual"}))
    enforce(db, environment="production")
    value = customer(product="primerep.annual", sandbox=True)
    production = customer()["subscriber"]["subscriptions"][PRODUCT]
    production["expires_date"] = (NOW + timedelta(days=2)).isoformat()
    value["subscriber"]["subscriptions"][PRODUCT] = production
    snapshot = verify(db, monkeypatch, value)
    assert snapshot["has_premium"] and snapshot["product_id"] == PRODUCT
    value["subscriber"]["entitlements"] = {}
    assert not verify(db, monkeypatch, value)["has_premium"]


def test_older_provider_snapshot_cannot_overwrite_newer_access(db, monkeypatch):
    enforce(db)
    verify(db, monkeypatch, customer())
    result = verify(db, monkeypatch, {"request_date": (NOW - timedelta(hours=1)).isoformat(), "subscriber": {"entitlements": {}}})
    assert result["has_premium"] is True


def test_authenticated_raw_body_hmac_and_replay_timestamp(db, monkeypatch):
    raw = b'{"event":{"id":"one"}}'
    with pytest.raises(service.SubscriptionAccessError):
        service.verify_webhook(raw, "incorrect", None)
    monkeypatch.setattr(settings, "REVENUECAT_WEBHOOK_SIGNING_SECRET", "signing-test")
    timestamp = str(int(NOW.timestamp()))
    digest = hmac.new(b"signing-test", timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    service.verify_webhook(raw, "Bearer webhook-test", f"t={timestamp},v1={digest}")
    for changed_raw, signature in [(raw + b" ", f"t={timestamp},v1={digest}"), (raw, f"t=1,v1={digest}")]:
        with pytest.raises(service.SubscriptionAccessError):
            service.verify_webhook(changed_raw, "Bearer webhook-test", signature)


def webhook(event_id="event-1", kind="RENEWAL", **extra):
    return json.dumps({"event": {"id": event_id, "type": kind, "app_id": "app-test", "environment": "PRODUCTION", "app_user_id": A, **extra}}).encode()


def test_duplicate_and_transfer_events_create_only_durable_known_account_jobs(db):
    for _ in range(2):
        service.receive_webhook(db, webhook())
    service.receive_webhook(db, webhook("transfer", "TRANSFER", transferred_from=[A], transferred_to=[B, "unknown", "00000000-0000-4000-8000-000000000099"]))
    db.commit()
    assert db.scalar(select(func.count()).select_from(BillingWebhookEvent)) == 2
    assert {job.user_id for job in db.scalars(select(BillingJob)).all()} == {A, B}
    assert db.scalar(select(func.count()).select_from(SubscriptionState)) == 0


def test_webhook_atomicity_wrong_app_and_deleted_account(db):
    with pytest.raises(service.SubscriptionAccessError):
        service.receive_webhook(db, webhook(app_id="unrelated-project"))
    assert db.scalar(select(func.count()).select_from(BillingWebhookEvent)) == 0
    db.delete(db.get(User, A))
    db.commit()
    service.receive_webhook(db, webhook())
    db.commit()
    assert db.scalar(select(func.count()).select_from(BillingJob)) == 0
    assert db.get(User, A) is None


def test_worker_reconciles_both_transfer_accounts_and_retries_outages(db, monkeypatch):
    enforce(db)
    verify(db, monkeypatch, customer())
    service.receive_webhook(db, webhook("transfer", "TRANSFER", transferred_from=[A], transferred_to=[B]))
    monkeypatch.setattr(revenuecat_client, "fetch_customer", lambda user_id: customer() if user_id == B else {"subscriber": {"entitlements": {}}})
    assert service.process_billing_job(db)
    db.commit()
    assert service.process_billing_job(db)
    db.commit()
    assert service.get_subscription(db, A)["has_premium"] is False
    assert service.get_subscription(db, B)["has_premium"] is True
    service.enqueue_reconciliation(db, B)
    monkeypatch.setattr(revenuecat_client, "fetch_customer", lambda _: (_ for _ in ()).throw(revenuecat_client.RevenueCatUnavailable("outage")))
    assert service.process_billing_job(db)
    db.commit()
    job = db.get(BillingJob, ("test", B, "reconcile"))
    assert job.attempts == 1 and job.status == "pending" and service._utc(job.next_attempt_at) > NOW


def test_erasure_survives_account_delete_and_prunes_on_completion(db, monkeypatch):
    verify(db, monkeypatch, customer())
    service.enqueue_customer_erasure(db, A)
    db.delete(db.get(User, A))
    db.commit()
    erased = []
    monkeypatch.setattr(revenuecat_client, "erase_customer", erased.append)
    assert service.process_billing_job(db)
    db.commit()
    assert erased == [A]
    assert db.get(SubscriptionState, ("test", A)) is None
    service.prune_billing_history(db, now=NOW + timedelta(days=8))
    db.commit()
    assert db.get(BillingJob, ("test", A, "erase")) is None


def test_fresh_late_purchase_reopens_retained_erasure_without_recreating_account(db, monkeypatch):
    service.enqueue_customer_erasure(db, A)
    db.delete(db.get(User, A))
    db.commit()
    erased = []
    monkeypatch.setattr(revenuecat_client, "erase_customer", erased.append)
    assert service.process_billing_job(db)
    db.commit()
    assert db.get(BillingJob, ("test", A, "erase")).status == "done"
    late = webhook("late-purchase", "INITIAL_PURCHASE")
    service.receive_webhook(db, late)
    db.commit()
    assert db.get(BillingJob, ("test", A, "erase"), populate_existing=True).status == "pending"
    assert service.process_billing_job(db)
    db.commit()
    assert erased == [A, A]
    service.receive_webhook(db, late)
    unknown = "00000000-0000-4000-8000-000000000099"
    service.receive_webhook(db, webhook("unrecognized-purchase", "INITIAL_PURCHASE", app_user_id=unknown))
    db.commit()
    assert service.process_billing_job(db) is False
    assert erased == [A, A]
    assert db.get(User, A) is None
    assert db.scalar(select(func.count()).select_from(SubscriptionState)) == 0
    assert db.scalar(select(func.count()).select_from(BillingJob).where(BillingJob.kind == "reconcile")) == 0
    assert db.get(BillingJob, ("test", unknown, "erase")) is None


def test_pruning_is_environment_scoped(db):
    for environment in ("test", "production"):
        db.add(BillingWebhookEvent(environment=environment, event_id="old", event_type="RENEWAL", received_at=NOW - timedelta(days=31)))
        db.add(BillingJob(environment=environment, user_id=A, kind="erase", status="done", attempts=0,
                         next_attempt_at=NOW, requested_at=NOW, completed_at=NOW - timedelta(days=8)))
    db.commit()
    service.prune_billing_history(db)
    db.commit()
    assert db.get(BillingWebhookEvent, ("test", "old")) is None
    assert db.get(BillingJob, ("test", A, "erase")) is None
    assert db.get(BillingWebhookEvent, ("production", "old")) is not None
    assert db.get(BillingJob, ("production", A, "erase")) is not None


def test_new_webhook_reopens_exhausted_job_without_resetting_existing_retries(db):
    db.add(BillingJob(environment="test", user_id=A, kind="reconcile", status="failed", attempts=12,
                     next_attempt_at=NOW + timedelta(hours=1), requested_at=NOW - timedelta(days=1)))
    db.commit()
    service.receive_webhook(db, webhook())
    db.commit()
    job = db.get(BillingJob, ("test", A, "reconcile"), populate_existing=True)
    assert job.status == "pending" and job.attempts == 0
    job.attempts, job.next_attempt_at = 3, NOW + timedelta(minutes=10)
    db.commit()
    service.receive_webhook(db, webhook())
    service.enqueue_reconciliation(db, A)
    db.commit()
    db.refresh(job)
    assert job.attempts == 3 and service._utc(job.next_attempt_at) == NOW + timedelta(minutes=10)


@pytest.fixture
def client(db):
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=A)

    @app.exception_handler(service.SubscriptionAccessError)
    async def subscription_error(request, error):
        return JSONResponse(status_code=error.status_code, content={"detail": str(error), "code": error.code})

    with TestClient(app) as client:
        yield client


def test_endpoint_contract_and_forged_claims_never_grant_access(client, db):
    response = client.get("/v1/users/me/subscription")
    assert response.status_code == 200 and response.json()["access_source"] == "legacy"
    enforce(db)
    response = client.post("/v1/users/me/subscription/reconcile", json={"has_premium": True, "user_id": B})
    assert response.status_code == 200 and response.json()["can_start_training"] is False
    assert db.get(SubscriptionState, ("test", B)) is None
    assert client.post("/v1/billing/revenuecat/webhook", content=webhook()).status_code == 401
    assert client.post("/v1/billing/revenuecat/webhook", content=webhook(), headers={"Authorization": "Bearer webhook-test"}).status_code == 200


def test_reconcile_limits_are_per_account_on_shared_connection(client, db):
    def signed_in_user(request: Request):
        return db.get(User, decode_access_token(request.headers["Authorization"][7:])["sub"])

    client.app.dependency_overrides[get_current_user] = signed_in_user
    account_a = {"Authorization": f"Bearer {create_access_token(A)}"}
    account_b = {"Authorization": f"Bearer {create_access_token(B)}"}
    for _ in range(6):
        assert client.post("/v1/users/me/subscription/reconcile", headers=account_a).status_code == 200
    assert client.post("/v1/users/me/subscription/reconcile", headers=account_a).status_code == 429
    assert client.post("/v1/users/me/subscription/reconcile", headers=account_b).status_code == 200
