from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.email_service import EmailMessage, EmailSender, get_email_sender
from app.core.settings import settings
from app.models.email_outbox import EmailOutbox
from app.models.password_reset_token import PasswordResetToken

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 8


def _cipher() -> Fernet:
    key = settings.EMAIL_OUTBOX_ENCRYPTION_KEY
    if not key:
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.JWT_SECRET.encode()).digest()).decode()
    return Fernet(key.encode())


def enqueue_email(
    db: Session,
    message: EmailMessage,
    *,
    kind: str,
    reset_token_id: str,
    expires_at: datetime,
) -> None:
    payload = json.dumps({
        "recipient": message.recipient,
        "subject": message.subject,
        "html": message.html,
        "idempotency_key": message.idempotency_key,
    }).encode()
    db.add(EmailOutbox(
        idempotency_key=message.idempotency_key,
        kind=kind,
        reset_token_id=reset_token_id,
        encrypted_message=_cipher().encrypt(payload),
        expires_at=expires_at,
    ))


def process_email(db: Session, sender: EmailSender, *, key: str | None = None) -> str | None:
    now = datetime.now(timezone.utc)
    query = db.query(EmailOutbox).filter(
        EmailOutbox.status == "pending",
        EmailOutbox.next_attempt_at <= now,
    )
    if key is not None:
        query = query.filter(EmailOutbox.idempotency_key == key)
    row = query.order_by(EmailOutbox.created_at, EmailOutbox.id).with_for_update(skip_locked=True).first()
    if row is None:
        return None

    reset = db.get(PasswordResetToken, row.reset_token_id) if row.kind == "reset" else None
    if row.expires_at <= now or (row.kind == "reset" and (reset is None or reset.used_at is not None)):
        row.status = "expired"
        row.encrypted_message = None
        db.commit()
        return "expired"

    try:
        message = EmailMessage(**json.loads(_cipher().decrypt(row.encrypted_message)))
        sender.send(message)
    except Exception as error:
        row.attempts += 1
        if row.attempts >= MAX_ATTEMPTS:
            row.status = "failed"
            row.encrypted_message = None
        else:
            row.next_attempt_at = now + timedelta(minutes=min(2 ** row.attempts, 30))
        db.commit()
        logger.warning("Email delivery deferred", extra={
            "email_event": row.kind,
            "failure_class": type(error).__name__,
        })
        return "failed" if row.status == "failed" else "retry"

    row.status = "sent"
    row.sent_at = now
    row.encrypted_message = None
    db.commit()
    return "sent"


def send_email_outbox(sender: EmailSender, key: str) -> None:
    with SessionLocal() as db:
        process_email(db, sender, key=key)


def run_worker_once(db: Session, *, limit: int = 100) -> dict[str, int]:
    sender = get_email_sender()
    result = {"sent": 0, "retry": 0, "expired": 0, "failed": 0}
    for _ in range(limit):
        outcome = process_email(db, sender)
        if outcome is None:
            break
        result[outcome] += 1
    pending, oldest = db.query(func.count(EmailOutbox.id), func.min(EmailOutbox.created_at)).filter(
        EmailOutbox.status == "pending"
    ).one()
    result["pending"] = pending
    result["oldestPendingSeconds"] = int((datetime.now(timezone.utc) - oldest).total_seconds()) if oldest else 0
    return result
