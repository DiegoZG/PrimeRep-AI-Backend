from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.rate_limit import user_limiter
from app.core.security.deps import get_current_user
from app.core.subscription_service import SubscriptionAccessError, get_subscription, receive_webhook, reconcile_subscription, verify_webhook
from app.models.user import User
from app.schemas.subscription import SubscriptionSnapshot

router = APIRouter(tags=["subscriptions"])


@router.get("/users/me/subscription", response_model=SubscriptionSnapshot)
def read_subscription(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    snapshot = get_subscription(db, str(current_user.id))
    db.commit()
    return snapshot


@router.post("/users/me/subscription/reconcile", response_model=SubscriptionSnapshot)
@user_limiter.limit("6/minute")
def reconcile(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        snapshot = reconcile_subscription(db, str(current_user.id))
    except SubscriptionAccessError:
        db.commit()
        raise
    db.commit()
    return snapshot


@router.post("/billing/revenuecat/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 262_144:
            raise SubscriptionAccessError(413, "billing_event_too_large", "Billing notification is too large.")
    body = bytes(raw)
    verify_webhook(body, request.headers.get("Authorization"), request.headers.get("X-RevenueCat-Webhook-Signature"))
    receive_webhook(db, body)
    db.commit()
    return {"received": True}
