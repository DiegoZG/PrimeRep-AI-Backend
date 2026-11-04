import hashlib
import hmac
import json
import random
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from uuid import UUID
from typing import Optional

from sqlalchemy import case, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core import revenuecat_client
from app.core.feature_flag_service import flag_environment, get_feature_flags
from app.core.settings import settings
from app.models.subscription import BillingJob, BillingWebhookEvent, SubscriptionState, SubscriptionTransitionGrant
from app.models.user import User


class SubscriptionAccessError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code, self.code = status_code, code
        super().__init__(message)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def _now():
    return datetime.now(timezone.utc)


def _date(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise revenuecat_client.RevenueCatUnavailable("invalid_provider_date")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("Timezone missing")
        return result.astimezone(timezone.utc)
    except ValueError as error:
        raise revenuecat_client.RevenueCatUnavailable("invalid_provider_date") from error


def _insert(db, model):
    return (sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert)(model)


def enqueue_reconciliation(db: Session, user_id: str, *, kind: str = "reconcile", now=None, new_event=False):
    now = now or _now()
    environment = flag_environment()
    insert = _insert(db, BillingJob).values(
        environment=environment, user_id=user_id, kind=kind, status="pending", attempts=0,
        next_attempt_at=now, requested_at=now,
    )
    reopen = or_(BillingJob.status == "done", BillingJob.status == "failed") if new_event else BillingJob.status == "done"
    db.execute(insert.on_conflict_do_update(
        index_elements=["environment", "user_id", "kind"],
        set_={"requested_at": now,
              "status": case((reopen, "pending"), else_=BillingJob.status),
              "attempts": case((reopen, 0), else_=BillingJob.attempts),
              "next_attempt_at": case((reopen, now), else_=BillingJob.next_attempt_at),
              "last_error": case((reopen, None), else_=BillingJob.last_error),
              "completed_at": None},
    ))


def enqueue_customer_erasure(db: Session, user_id: str):
    db.execute(delete(BillingJob).where(BillingJob.environment == flag_environment(), BillingJob.user_id == user_id))
    enqueue_reconciliation(db, user_id, kind="erase")


def _transition(db, user, flags, now):
    launch = flags["subscriptions_launch_at"]
    if not flags["launch_locked"] or not launch or _utc(user.created_at) >= launch:
        return None
    grant = db.get(SubscriptionTransitionGrant, (flags["environment"], user.id))
    if not grant:
        grant = SubscriptionTransitionGrant(environment=flags["environment"], user_id=user.id,
                                            launch_at=launch, expires_at=flags["transition_ends_at"])
        insert = _insert(db, SubscriptionTransitionGrant).values(
            environment=grant.environment, user_id=grant.user_id, launch_at=launch, expires_at=grant.expires_at,
        )
        db.execute(insert.on_conflict_do_nothing(index_elements=["environment", "user_id"]))
        grant = db.get(SubscriptionTransitionGrant, (flags["environment"], user.id), populate_existing=True)
    return _utc(grant.expires_at) if grant else None


def get_subscription(db: Session, user_id: str, *, now=None) -> dict:
    now = now or _now()
    user = db.get(User, user_id)
    if not user:
        raise SubscriptionAccessError(404, "account_not_found", "Account no longer exists.")
    flags = get_feature_flags(db, user_id)
    state = db.get(SubscriptionState, (flags["environment"], user_id), populate_existing=True)
    transition = _transition(db, user, flags, now)
    expiry = _utc(state.expires_at) if state else None
    grace = _utc(state.grace_expires_at) if state else None
    validity = max((date for date in (expiry, grace) if date), default=None)
    has_premium = bool(state and state.verified_at and state.billing_status in {"trial", "active", "grace"} and validity and validity > now)
    verified_at = _utc(state.verified_at) if state else None
    verification = state.verification_status if state else "unverified"
    if not revenuecat_client.configured():
        verification = "not_configured"
    known_absence = bool(state and verified_at and verification == "verified" and (
        state.billing_status == "none" or (validity and validity <= now and verified_at >= validity)
    ))
    enforcement = flags["flags"]["subscriptions_enforcement_enabled"]
    if not enforcement:
        allowed, source, access_expiry = True, "legacy", None
    elif has_premium:
        allowed, source, access_expiry = True, "subscription", validity
    elif transition and transition > now:
        allowed, source, access_expiry = True, "transition", transition
    elif known_absence:
        allowed, source, access_expiry = False, "none", None
    else:
        allowed, source, access_expiry = None, "unknown", None
    return {
        "environment": flags["environment"], "flags_version": flags["version"], "flags": flags["flags"],
        "billing_status": state.billing_status if state else "unknown", "verification_status": verification,
        "verified_at": verified_at, "product_id": state.product_id if state else None,
        "expires_at": expiry, "grace_expires_at": grace, "will_renew": state.will_renew if state else None,
        "management_url": state.management_url if state else None, "has_premium": has_premium,
        "can_start_training": allowed, "access_source": source, "access_expires_at": access_expiry,
        "transition_ends_at": transition, "provider_configured": revenuecat_client.configured(),
    }


def training_access_allowed(db: Session, user_id: str) -> Optional[bool]:
    return get_subscription(db, user_id)["can_start_training"]


def enqueue_due_reconciliation(db: Session, user_id: str, *, enforcement=False, now=None):
    now = now or _now()
    state = db.get(SubscriptionState, (flag_environment(), user_id), populate_existing=True)
    if revenuecat_client.configured() and ((state and (not state.next_check_at or _utc(state.next_check_at) <= now)) or (not state and enforcement)):
        enqueue_reconciliation(db, user_id, now=now)


def _normalise_customer(value, user_id, now):
    customer = value.get("subscriber")
    if not isinstance(customer, dict) or not isinstance(customer.get("entitlements", {}), dict):
        raise revenuecat_client.RevenueCatUnavailable("invalid_provider_response")
    snapshot_at = _date(value.get("request_date")) or now
    if snapshot_at > now + timedelta(minutes=5):
        raise revenuecat_client.RevenueCatUnavailable("invalid_provider_date")
    entitlement = customer.get("entitlements", {}).get("premium")
    none = {"billing_status": "none", "product_id": None, "expires_at": None,
            "grace_expires_at": None, "will_renew": None, "management_url": None, "provider_snapshot_at": snapshot_at}
    if entitlement is None:
        return none
    if not isinstance(entitlement, dict):
        raise revenuecat_client.RevenueCatUnavailable("invalid_provider_response")
    entitlement_product = entitlement.get("product_identifier")
    purchases = customer.get("subscriptions", {})
    if not isinstance(purchases, dict):
        raise revenuecat_client.RevenueCatUnavailable("invalid_provider_response")
    candidates, invalid = [], False
    management = customer.get("management_url")
    if not isinstance(management, str) or len(management) > 500 or urlparse(management).scheme != "https" or urlparse(management).hostname != "apps.apple.com":
        management = None
    for product, subscription in purchases.items():
        if product not in settings.REVENUECAT_PRODUCT_IDS:
            continue
        if not isinstance(subscription, dict) or not isinstance(subscription.get("is_sandbox"), bool):
            invalid = True
            continue
        if flag_environment() == "production" and subscription["is_sandbox"] and user_id not in settings.REVENUECAT_PRODUCTION_TEST_USER_IDS:
            continue
        try:
            selected = entitlement_product == product
            expiry = _date(subscription.get("expires_date", entitlement.get("expires_date") if selected else None))
            grace = _date(subscription.get("grace_period_expires_date", entitlement.get("grace_period_expires_date") if selected else None))
            if not expiry:
                raise revenuecat_client.RevenueCatUnavailable("missing_subscription_expiry")
            purchase = _date(subscription.get("purchase_date"))
            refunded = _date(subscription.get("refunded_at"))
            if refunded and (not purchase or refunded >= purchase):
                expiry, grace = min(expiry, refunded), None
            valid_until = max(date for date in (expiry, grace) if date)
            status = "expired" if valid_until <= now else "grace" if expiry <= now else "trial" if subscription.get("period_type") == "trial" else "active"
            candidates.append({"billing_status": status, "product_id": product, "expires_at": expiry, "grace_expires_at": grace,
                               "will_renew": subscription.get("unsubscribe_detected_at") is None, "management_url": management,
                               "provider_snapshot_at": snapshot_at})
        except revenuecat_client.RevenueCatUnavailable:
            invalid = True
    active = [candidate for candidate in candidates if candidate["billing_status"] != "expired"]
    if not active and invalid:
        raise revenuecat_client.RevenueCatUnavailable("invalid_provider_response")
    return max(active or candidates, key=lambda candidate: max(date for date in (candidate["expires_at"], candidate["grace_expires_at"]) if date)) if candidates else none


def reconcile_subscription(db: Session, user_id: str, *, now=None) -> dict:
    now = now or _now()
    get_feature_flags(db, user_id)
    # Lock the account before the provider fetch so concurrent reconciliation cannot apply an older response last.
    user = db.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))
    if not user:
        raise SubscriptionAccessError(404, "account_not_found", "Account no longer exists.")
    environment = flag_environment()
    state = db.get(SubscriptionState, (environment, user_id), populate_existing=True)
    if not state:
        state = SubscriptionState(environment=environment, user_id=user_id)
        db.add(state)
    try:
        normalised = _normalise_customer(revenuecat_client.fetch_customer(user_id), user_id, now)
        if not state.provider_snapshot_at or normalised["provider_snapshot_at"] >= _utc(state.provider_snapshot_at):
            for key, value in normalised.items():
                setattr(state, key, value)
            state.verified_at = now
            state.verification_status = "verified"
            validity = max((date for date in (state.expires_at, state.grace_expires_at) if date), default=None)
            state.next_check_at = min(now + timedelta(hours=1), validity) if validity and validity > now else now + timedelta(hours=1)
            user.subscription_tier = "premium" if state.billing_status in {"trial", "active", "grace"} else "free"
    except revenuecat_client.RevenueCatUnavailable as error:
        state.verification_status = "not_configured" if not revenuecat_client.configured() else "unavailable"
        state.next_check_at = now + timedelta(minutes=1)
        db.flush()
        raise SubscriptionAccessError(503, "subscription_verification_unavailable", "Couldn’t verify subscription access. Try again shortly.") from error
    db.flush()
    return get_subscription(db, user_id, now=now)


def require_training_access(db: Session, user_id: str):
    snapshot = get_subscription(db, user_id)
    if snapshot["can_start_training"] is True:
        return snapshot
    snapshot = reconcile_subscription(db, user_id)
    if snapshot["can_start_training"] is True:
        return snapshot
    if snapshot["can_start_training"] is None:
        raise SubscriptionAccessError(503, "subscription_verification_unavailable", "Couldn’t verify subscription access. Try again shortly.")
    raise SubscriptionAccessError(403, "subscription_required", "A subscription is required to start new training.")


def verify_webhook(raw: bytes, authorization: Optional[str], signature: Optional[str], *, now=None):
    if not settings.REVENUECAT_WEBHOOK_AUTHORIZATION or not settings.REVENUECAT_APP_IDS:
        raise SubscriptionAccessError(503, "billing_not_configured", "Billing notifications are not configured.")
    if not hmac.compare_digest((authorization or "").encode(), settings.REVENUECAT_WEBHOOK_AUTHORIZATION.encode()):
        raise SubscriptionAccessError(401, "invalid_webhook_authorization", "Invalid billing notification authorization.")
    if settings.REVENUECAT_WEBHOOK_SIGNING_SECRET:
        try:
            parts = dict(part.split("=", 1) for part in (signature or "").split(","))
            timestamp = int(parts["t"])
            expected = hmac.new(settings.REVENUECAT_WEBHOOK_SIGNING_SECRET.encode(), str(timestamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
            valid = abs((now or _now()).timestamp() - timestamp) <= 300 and hmac.compare_digest(expected, parts["v1"])
        except (ValueError, KeyError):
            valid = False
        if not valid:
            raise SubscriptionAccessError(401, "invalid_webhook_signature", "Invalid billing notification signature.")


def receive_webhook(db: Session, raw: bytes):
    try:
        value = json.loads(raw)
        event = value["event"]
        event_id, event_type = event["id"], event["type"]
        if not isinstance(event_id, str) or not event_id or len(event_id) > 200 or not isinstance(event_type, str) or len(event_type) > 80:
            raise ValueError("Invalid event")
        if event.get("app_id") not in settings.REVENUECAT_APP_IDS:
            raise ValueError("Wrong app")
        if event.get("environment") not in {"SANDBOX", "PRODUCTION"}:
            raise ValueError("Invalid environment")
        identities = event.get("transferred_from", []) + event.get("transferred_to", []) if event_type == "TRANSFER" else [event.get("app_user_id")]
        if not isinstance(identities, list) or len(identities) > 100:
            raise ValueError("Invalid identities")
        ids = set()
        for identity in identities:
            try:
                ids.add(str(UUID(identity)))
            except (ValueError, TypeError, AttributeError):
                continue
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise SubscriptionAccessError(400, "invalid_billing_event", "Invalid billing notification.") from error
    now = _now()
    insert = _insert(db, BillingWebhookEvent).values(environment=flag_environment(), event_id=event_id, event_type=event_type, received_at=now)
    received = db.execute(insert.on_conflict_do_nothing(index_elements=["environment", "event_id"]).returning(BillingWebhookEvent.event_id)).scalar()
    if received:
        for user_id in sorted(ids):
            if db.get(User, user_id):
                enqueue_reconciliation(db, user_id, now=now, new_event=True)
            elif db.get(BillingJob, (flag_environment(), user_id, "erase")):
                enqueue_reconciliation(db, user_id, kind="erase", now=now, new_event=True)


def schedule_due_reconciliations(db: Session, *, limit=100, now=None):
    now = now or _now()
    states = db.scalars(select(SubscriptionState).where(
        SubscriptionState.environment == flag_environment(), SubscriptionState.next_check_at <= now,
    ).order_by(SubscriptionState.next_check_at).limit(limit)).all()
    for state in states:
        previous_check = state.next_check_at
        enqueue_reconciliation(db, state.user_id, now=now)
        db.execute(update(SubscriptionState).where(
            SubscriptionState.environment == state.environment, SubscriptionState.user_id == state.user_id,
            SubscriptionState.next_check_at == previous_check,
        ).values(next_check_at=now + timedelta(minutes=5)).execution_options(synchronize_session=False))
    return len(states)


def process_billing_job(db: Session, *, now=None):
    now = now or _now()
    job = db.scalar(select(BillingJob).where(
        BillingJob.environment == flag_environment(), BillingJob.status == "pending", BillingJob.next_attempt_at <= now,
    ).order_by(BillingJob.next_attempt_at).limit(1).with_for_update(skip_locked=True))
    if not job:
        return False
    try:
        if job.kind == "erase":
            revenuecat_client.erase_customer(job.user_id)
        elif db.get(User, job.user_id):
            reconcile_subscription(db, job.user_id, now=now)
        job.status, job.completed_at, job.last_error = "done", now, None
    except (SubscriptionAccessError, revenuecat_client.RevenueCatUnavailable):
        job.attempts += 1
        job.last_error = "provider_unavailable"
        job.next_attempt_at = now + timedelta(seconds=min(3600, 30 * 2 ** min(job.attempts, 7)) + random.randint(0, 20))
        if job.attempts >= 12:
            job.status = "failed"
    db.flush()
    return True


def prune_billing_history(db: Session, *, now=None):
    now = now or _now()
    db.execute(delete(BillingWebhookEvent).where(BillingWebhookEvent.environment == flag_environment(), BillingWebhookEvent.received_at < now - timedelta(days=30)))
    db.execute(delete(BillingJob).where(BillingJob.environment == flag_environment(), BillingJob.status == "done", BillingJob.completed_at < now - timedelta(days=7)))
