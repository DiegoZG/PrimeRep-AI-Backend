from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.coach_feed_service import (
    AI_BATCH_LIMIT,
    AI_SAFE_KINDS,
    _event,
    _now,
    enrich_pending_items,
    local_reminder_utc,
    purge_expired,
    reconcile_feed,
)
from app.core.settings import settings
from app.models.coach import (
    CoachFeedItem,
    CoachNotificationDelivery,
    CoachNotificationJob,
    CoachPreference,
)
from app.models.push_token import PushToken
from app.models.user import User


PUSH_URL = "https://exp.host/--/api/v2/push/send"
RECEIPTS_URL = "https://exp.host/--/api/v2/push/getReceipts"
MAX_BATCH_SIZE = 100
MAX_ATTEMPTS = 5
RECEIPT_DELAY = timedelta(minutes=15)
LOCK_TIMEOUT = timedelta(minutes=10)
WORKER_USER_LIMIT = 100
AI_WORKER_ITEM_LIMIT = 100
PURGE_LIMIT = 500


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if settings.EXPO_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {settings.EXPO_ACCESS_TOKEN}"
    return headers


def _post_json(url: str, payload: Any) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers=_headers(),
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode())
    except HTTPError as error:
        retryable = error.code == 429 or error.code >= 500
        raise ExpoRequestError("http_retryable" if retryable else "http_terminal", retryable) from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ExpoRequestError("network", True) from error


class ExpoRequestError(Exception):
    def __init__(self, code: str, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def enqueue_due_jobs(
    db: Session,
    now: Optional[datetime] = None,
    *,
    limit: int = WORKER_USER_LIMIT,
) -> int:
    now = now or _now()
    started = _now()
    count = 0
    capped_limit = max(1, min(limit, WORKER_USER_LIMIT))
    selected_user_ids = set()
    processed_users = 0
    next_queue = "dirty"
    empty_turns = 0
    while processed_users < capped_limit and empty_turns < 2:
        preference = _claim_next_preference(
            db,
            queue=next_queue,
            now=now,
            excluded_user_ids=selected_user_ids,
        )
        next_queue = "notification" if next_queue == "dirty" else "dirty"
        if preference is None:
            db.rollback()
            empty_turns += 1
            continue
        empty_turns = 0
        preference_user_id = preference.user_id
        selected_user_ids.add(preference_user_id)
        try:
            user_job_count = _enqueue_for_preference(db, preference, now)
            preference.last_reconciled_at = now
            db.commit()
            count += user_job_count
            processed_users += 1
        except Exception as error:
            db.rollback()
            processed_users += 1
            _event(
                "coach_notification_enqueue_user",
                source="worker",
                outcome="failed",
                user=_opaque(preference_user_id),
                reason=type(error).__name__,
            )
    queue_depth = db.query(CoachNotificationJob).filter(
        CoachNotificationJob.status.in_(["pending", "retry"])
    ).count()
    _event(
        "coach_notification_enqueue",
        source="worker",
        outcome="success",
        userCount=processed_users,
        jobCount=count,
        queueDepth=queue_depth,
        durationMs=int((_now() - started).total_seconds() * 1000),
    )
    return count


def _claim_next_preference(
    db: Session,
    *,
    queue: str,
    now: datetime,
    excluded_user_ids: set[str],
) -> Optional[CoachPreference]:
    query = db.query(CoachPreference)
    if excluded_user_ids:
        query = query.filter(CoachPreference.user_id.notin_(excluded_user_ids))
    if queue == "dirty":
        query = query.filter(CoachPreference.reconciliation_requested_at.is_not(None)).order_by(
            CoachPreference.reconciliation_requested_at.asc(),
            CoachPreference.user_id.asc(),
        )
    else:
        query = query.filter(
            CoachPreference.notifications_enabled.is_(True),
            or_(
                CoachPreference.last_reconciled_at.is_(None),
                CoachPreference.last_reconciled_at < now,
            ),
        ).order_by(
            CoachPreference.last_reconciled_at.asc().nullsfirst(),
            CoachPreference.user_id.asc(),
        )
    return query.with_for_update(skip_locked=True).first()


def _enqueue_for_preference(
    db: Session, preference: CoachPreference, now: datetime
) -> int:
    count = 0
    local_now = now.astimezone(ZoneInfo(preference.time_zone))
    local_date = local_now.date()
    reconcile_feed(
        db,
        preference.user_id,
        local_date,
        commit=False,
        source="notification_worker",
    )
    if not preference.notifications_enabled:
        return 0

    review_items = (
        db.query(CoachFeedItem)
        .filter(
            CoachFeedItem.user_id == preference.user_id,
            CoachFeedItem.kind == "program_review",
            CoachFeedItem.dismissed_at.is_(None),
            CoachFeedItem.expires_at > now,
        )
        .all()
    )
    for item in review_items:
        count += _insert_job(
            db,
            user_id=preference.user_id,
            item=item,
            job_type="equipment_review",
            scheduled_at=now,
            local_delivery_date=local_date,
        )

    reminder_at = local_reminder_utc(local_date, preference.reminder_time, preference.time_zone)
    if reminder_at <= now:
        reminder = (
            db.query(CoachFeedItem)
            .filter(
                CoachFeedItem.user_id == preference.user_id,
                CoachFeedItem.kind.in_(["missed_workout", "upcoming_workout"]),
                CoachFeedItem.dismissed_at.is_(None),
                CoachFeedItem.expires_at > now,
            )
            .order_by(CoachFeedItem.priority.desc(), CoachFeedItem.created_at.desc())
            .first()
        )
        if reminder:
            count += _insert_job(
                db,
                user_id=preference.user_id,
                item=reminder,
                job_type="scheduled_daily",
                scheduled_at=reminder_at,
                local_delivery_date=local_date,
            )
    return count


def process_ai_users(db: Session, limit: int = AI_WORKER_ITEM_LIMIT) -> int:
    processed = 0
    now = _now()
    stale_lock = now - LOCK_TIMEOUT
    user_ids = [
        row[0]
        for row in (
            db.query(CoachFeedItem.user_id)
            .join(User, User.id == CoachFeedItem.user_id)
            .filter(
                User.subscription_tier == "premium",
                User.coach_insights_enabled.is_(True),
                CoachFeedItem.kind.in_(AI_SAFE_KINDS),
                CoachFeedItem.expires_at > now,
                or_(
                    CoachFeedItem.ai_status == "pending",
                    (
                        (CoachFeedItem.ai_status == "processing")
                        & (CoachFeedItem.ai_locked_at < stale_lock)
                    ),
                ),
            )
            .distinct()
            .order_by(CoachFeedItem.user_id)
            .limit(max(1, min(limit, WORKER_USER_LIMIT)))
            .all()
        )
    ]
    remaining = max(1, min(limit, AI_WORKER_ITEM_LIMIT))
    for user_id in user_ids:
        if remaining <= 0:
            break
        item_count = enrich_pending_items(
            db, user_id, limit=min(AI_BATCH_LIMIT, remaining)
        )
        processed += item_count
        remaining -= item_count
    return processed


def _insert_job(
    db: Session,
    *,
    user_id: str,
    item: CoachFeedItem,
    job_type: str,
    scheduled_at: datetime,
    local_delivery_date,
) -> int:
    existing = (
        db.query(CoachNotificationJob)
        .filter(CoachNotificationJob.feed_item_id == item.id)
        .with_for_update()
        .first()
    )
    if existing is not None:
        if existing.status != "cancelled":
            return 0
        try:
            with db.begin_nested():
                db.query(CoachNotificationDelivery).filter(
                    CoachNotificationDelivery.job_id == existing.id,
                    CoachNotificationDelivery.status == "retry",
                ).update(
                    {"retry_at": scheduled_at},
                    synchronize_session=False,
                )
                existing.job_type = job_type
                existing.scheduled_at = scheduled_at
                existing.local_delivery_date = local_delivery_date
                existing.status = "pending"
                existing.attempts = 0
                existing.retry_at = None
                existing.locked_at = None
                db.flush()
            return 1
        except IntegrityError:
            db.expire(existing)
            return 0
    job = CoachNotificationJob(
        id=str(uuid.uuid4()),
        user_id=user_id,
        feed_item_id=item.id,
        job_type=job_type,
        scheduled_at=scheduled_at,
        local_delivery_date=local_delivery_date,
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
        return 1
    except IntegrityError:
        return 0


def lock_due_jobs(db: Session, now: Optional[datetime] = None, limit: int = 100) -> list[CoachNotificationJob]:
    now = now or _now()
    stale_lock = now - LOCK_TIMEOUT
    jobs = (
        db.query(CoachNotificationJob)
        .filter(
            CoachNotificationJob.scheduled_at <= now,
            or_(CoachNotificationJob.retry_at.is_(None), CoachNotificationJob.retry_at <= now),
            or_(
                CoachNotificationJob.status.in_(["pending", "retry"]),
                (
                    (CoachNotificationJob.status == "processing")
                    & (CoachNotificationJob.locked_at < stale_lock)
                ),
            ),
        )
        .order_by(CoachNotificationJob.scheduled_at.asc(), CoachNotificationJob.id.asc())
        .with_for_update(skip_locked=True)
        .limit(min(MAX_BATCH_SIZE, limit))
        .all()
    )
    for job in jobs:
        job.status = "processing"
        job.locked_at = now
        job.attempts += 1
    db.commit()
    return jobs


def send_due_jobs(db: Session, now: Optional[datetime] = None) -> int:
    now = now or _now()
    jobs = lock_due_jobs(db, now)
    sent = 0
    for job in jobs:
        started = _now()
        item = db.get(CoachFeedItem, job.feed_item_id)
        preference = db.get(CoachPreference, job.user_id)
        from app.core.coach_feed_service import is_item_current

        if (
            not item
            or not preference
            or not preference.notifications_enabled
            or item.dismissed_at
            or item.expires_at <= now
            or not is_item_current(db, item)
        ):
            job.status = "cancelled"
            job.locked_at = None
            db.commit()
            _event(
                "coach_notification_send",
                source="worker",
                outcome="cancelled",
                reason="stale_or_ineligible",
                attempt=job.attempts,
                durationMs=int((_now() - started).total_seconds() * 1000),
            )
            continue
        tokens = db.query(PushToken).filter(PushToken.user_id == job.user_id).all()
        if not tokens:
            job.status = "failed"
            job.locked_at = None
            db.commit()
            _event(
                "coach_notification_send",
                source="worker",
                outcome="failed",
                reason="no_registered_token",
                kind=item.kind,
                attempt=job.attempts,
                durationMs=int((_now() - started).total_seconds() * 1000),
            )
            continue
        title, body = _copy(job.job_type, item.kind)
        pending_tokens = []
        for token in tokens:
            delivery = _delivery_for(db, job.id, token.token)
            if delivery is None or (
                delivery.status == "retry"
                and delivery.attempts < MAX_ATTEMPTS
                and (delivery.retry_at is None or delivery.retry_at <= now)
            ):
                pending_tokens.append(token)
        for offset in range(0, len(pending_tokens), MAX_BATCH_SIZE):
            chunk = pending_tokens[offset : offset + MAX_BATCH_SIZE]
            payloads = [
                {
                    "to": token.token,
                    "sound": "default",
                    "title": title,
                    "body": body,
                    "data": {"version": 1, "type": "coach_item", "coachItemId": item.id},
                }
                for token in chunk
            ]
            try:
                response = _post_json(PUSH_URL, payloads)
                tickets = response.get("data") if isinstance(response, dict) else None
                if not isinstance(tickets, list) or len(tickets) != len(chunk):
                    raise ExpoRequestError("malformed_ticket_response", True)
                for token, ticket in zip(chunk, tickets):
                    _record_ticket(db, job, token, ticket, now)
            except ExpoRequestError as error:
                for token in chunk:
                    _record_delivery_failure(
                        db, job, token, now, error.code, retryable=error.retryable
                    )
            db.commit()

        deliveries = db.query(CoachNotificationDelivery).filter_by(job_id=job.id).all()
        current_token_hashes = {
            _token_hash(row[0])
            for row in db.query(PushToken.token)
            .filter(PushToken.user_id == job.user_id)
            .all()
        }
        current_deliveries = [
            row for row in deliveries if row.push_token_hash in current_token_hashes
        ]
        for delivery in current_deliveries:
            if delivery.status == "retry" and delivery.attempts >= MAX_ATTEMPTS:
                delivery.status = "failed"
                delivery.retry_at = None
        retry_at = [
            row.retry_at
            for row in current_deliveries
            if row.status == "retry"
            and row.attempts < MAX_ATTEMPTS
            and row.retry_at
        ]
        if retry_at:
            job.status = "retry"
            job.retry_at = min(retry_at)
        elif current_token_hashes and len(current_deliveries) == len(current_token_hashes) and all(
            row.status in {"ticketed", "delivered"} for row in current_deliveries
        ):
            job.status = "sent"
            job.retry_at = None
            sent += 1
        elif len(current_deliveries) == len(current_token_hashes):
            job.status = "failed"
            job.retry_at = None
        else:
            job.status = "retry" if job.attempts < MAX_ATTEMPTS else "failed"
            job.retry_at = now + timedelta(minutes=2 ** job.attempts) if job.status == "retry" else None
        job.locked_at = None
        db.commit()
        _event(
            "coach_notification_send",
            source="expo",
            outcome=job.status,
            kind=item.kind,
            attempt=job.attempts,
            ticketCount=sum(row.status == "ticketed" for row in current_deliveries),
            durationMs=int((_now() - started).total_seconds() * 1000),
        )
    return sent


def _copy(job_type: str, item_kind: str) -> tuple[str, str]:
    if job_type == "equipment_review":
        return "PrimeRep", "Your program needs an equipment review."
    if item_kind == "upcoming_workout":
        return "PrimeRep", "Today’s workout is ready."
    return "PrimeRep", "Review your training week."


def _record_ticket(
    db: Session,
    job: CoachNotificationJob,
    token: PushToken,
    ticket: dict,
    now: datetime,
) -> None:
    if not isinstance(ticket, dict):
        _record_delivery_failure(db, job, token, now, "malformed_ticket", retryable=True)
        return
    delivery = _delivery_for(db, job.id, token.token)
    if delivery is None:
        delivery = CoachNotificationDelivery(
            id=str(uuid.uuid4()),
            job_id=job.id,
            push_token_hash=_token_hash(token.token),
            attempts=0,
        )
        db.add(delivery)
    delivery.attempts += 1
    delivery.retry_at = None
    if ticket.get("status") == "ok" and isinstance(ticket.get("id"), str):
        delivery.status = "ticketed"
        delivery.expo_ticket_id = ticket["id"]
        delivery.ticketed_at = now
        delivery.next_receipt_at = now + RECEIPT_DELAY
        delivery.receipt_attempts = 0
        delivery.error_code = None
    else:
        details = ticket.get("details") or {}
        code = str(details.get("error") or "ticket_rejected")[:80]
        delivery.error_code = code
        if code == "DeviceNotRegistered":
            delivery.status = "failed"
            db.delete(token)
            _event("coach_push_token_removed", reason="DeviceNotRegistered")
        elif code in {"MessageRateExceeded", "ExpoServerError"}:
            _set_delivery_retry(delivery, job, now)
        else:
            delivery.status = "failed"
            delivery.next_receipt_at = None


def _delivery_for(
    db: Session, job_id: str, raw_token: str
) -> Optional[CoachNotificationDelivery]:
    return (
        db.query(CoachNotificationDelivery)
        .filter_by(job_id=job_id, push_token_hash=_token_hash(raw_token))
        .first()
    )


def _record_delivery_failure(
    db: Session,
    job: CoachNotificationJob,
    token: PushToken,
    now: datetime,
    code: str,
    *,
    retryable: bool,
) -> None:
    delivery = _delivery_for(db, job.id, token.token)
    if delivery is None:
        delivery = CoachNotificationDelivery(
            id=str(uuid.uuid4()),
            job_id=job.id,
            push_token_hash=_token_hash(token.token),
            attempts=0,
        )
        db.add(delivery)
    delivery.attempts += 1
    delivery.error_code = code[:80]
    if retryable:
        _set_delivery_retry(delivery, job, now)
    else:
        delivery.status = "failed"
        delivery.retry_at = None
        delivery.next_receipt_at = None


def _set_delivery_retry(
    delivery: CoachNotificationDelivery, job: CoachNotificationJob, now: datetime
) -> None:
    if delivery.attempts < MAX_ATTEMPTS:
        delivery.status = "retry"
        delivery.retry_at = now + timedelta(minutes=2 ** max(1, delivery.attempts))
        delivery.next_receipt_at = None
    else:
        delivery.status = "failed"
        delivery.retry_at = None
        delivery.next_receipt_at = None


def poll_receipts(db: Session, now: Optional[datetime] = None) -> int:
    now = now or _now()
    started = _now()
    deliveries = (
        db.query(CoachNotificationDelivery)
        .filter(
            CoachNotificationDelivery.status == "ticketed",
            CoachNotificationDelivery.next_receipt_at <= now,
        )
        .order_by(CoachNotificationDelivery.next_receipt_at.asc())
        .with_for_update(skip_locked=True)
        .limit(MAX_BATCH_SIZE)
        .all()
    )
    ids = [row.expo_ticket_id for row in deliveries if row.expo_ticket_id]
    if not ids:
        return 0
    try:
        response = _post_json(RECEIPTS_URL, {"ids": ids})
    except ExpoRequestError as error:
        for delivery in deliveries:
            _defer_receipt(delivery, now, error.code)
        db.commit()
        _event(
            "coach_notification_receipts",
            source="expo",
            outcome="retry",
            reason=error.code,
            receiptCount=len(deliveries),
            durationMs=int((_now() - started).total_seconds() * 1000),
        )
        return 0
    receipts = response.get("data") if isinstance(response, dict) else None
    if not isinstance(receipts, dict):
        for delivery in deliveries:
            _defer_receipt(delivery, now, "malformed_receipts")
        db.commit()
        _event(
            "coach_notification_receipts",
            source="expo",
            outcome="retry",
            reason="malformed_receipts",
            receiptCount=len(deliveries),
            durationMs=int((_now() - started).total_seconds() * 1000),
        )
        return 0
    checked = 0
    job_ids = {row.job_id for row in deliveries}
    user_ids = {
        row[0]
        for row in db.query(CoachNotificationJob.user_id)
        .filter(CoachNotificationJob.id.in_(job_ids))
        .all()
    }
    tokens_by_hash = {
        _token_hash(row.token): row
        for row in db.query(PushToken).filter(PushToken.user_id.in_(user_ids)).all()
    }
    for delivery in deliveries:
        receipt = receipts.get(delivery.expo_ticket_id)
        if not receipt:
            _defer_receipt(delivery, now, "receipt_missing")
            continue
        checked += 1
        delivery.receipt_attempts += 1
        delivery.receipt_checked_at = now
        delivery.next_receipt_at = None
        if receipt.get("status") == "ok":
            delivery.status = "delivered"
            delivery.error_code = None
        else:
            code = str((receipt.get("details") or {}).get("error") or "receipt_rejected")[:80]
            delivery.error_code = code
            if code == "DeviceNotRegistered" and delivery.push_token_hash in tokens_by_hash:
                delivery.status = "failed"
                db.delete(tokens_by_hash[delivery.push_token_hash])
                _event("coach_push_token_removed", reason="DeviceNotRegistered")
            elif code in {"MessageRateExceeded", "ExpoServerError"}:
                job = db.get(CoachNotificationJob, delivery.job_id)
                if job is not None:
                    _set_delivery_retry(delivery, job, now)
                    if delivery.status == "retry":
                        job.status = "retry"
                        job.retry_at = delivery.retry_at
            else:
                delivery.status = "failed"
    db.commit()
    _event(
        "coach_notification_receipts",
        source="expo",
        outcome="checked",
        receiptCount=checked,
        missingCount=len(deliveries) - checked,
        durationMs=int((_now() - started).total_seconds() * 1000),
    )
    return checked


def _defer_receipt(
    delivery: CoachNotificationDelivery, now: datetime, reason: str
) -> None:
    delivery.receipt_attempts += 1
    delivery.error_code = reason[:80]
    if delivery.receipt_attempts >= MAX_ATTEMPTS:
        delivery.status = "failed"
        delivery.next_receipt_at = None
        delivery.receipt_checked_at = now
        return
    delivery.next_receipt_at = now + timedelta(
        minutes=2 ** max(1, delivery.receipt_attempts)
    )


def run_worker_once(db: Session, now: Optional[datetime] = None) -> dict[str, int]:
    now = now or _now()
    jobs = enqueue_due_jobs(db, now)
    ai_items = process_ai_users(db)
    sent = send_due_jobs(db, now)
    receipts = poll_receipts(db, now)
    items, deliveries = purge_expired(db, limit=PURGE_LIMIT)
    return {
        "jobsEnqueued": jobs,
        "jobsSent": sent,
        "aiItemsProcessed": ai_items,
        "receiptsChecked": receipts,
        "itemsPurged": items,
        "deliveriesPurged": deliveries,
    }
