from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security.emails import normalize_email
from app.core.security.passwords import hash_password
from app.core.settings import settings
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User

RESET_TOKEN_LIFETIME = timedelta(minutes=30)
RESET_REQUEST_COOLDOWN = timedelta(minutes=1)
RESET_TOKEN_RETENTION = timedelta(days=30)
RESET_TOKEN_CLEANUP_LIMIT = 100


class InvalidPasswordResetToken(Exception):
    pass


@dataclass(frozen=True)
class PasswordResetDelivery:
    reset_id: str
    recipient: str
    preferred_name: str
    reset_url: str


@dataclass(frozen=True)
class PasswordChangedDelivery:
    reset_id: str
    recipient: str
    preferred_name: str


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def request_password_reset(db: Session, email: str) -> PasswordResetDelivery | None:
    normalized_email = normalize_email(email)
    user = (
        db.query(User)
        .filter(User.email == normalized_email)
        .with_for_update()
        .first()
    )
    if user is None:
        return None

    now = datetime.now(timezone.utc)
    stale_ids = (
        select(PasswordResetToken.id)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.expires_at < now - RESET_TOKEN_RETENTION,
        )
        .order_by(PasswordResetToken.expires_at)
        .limit(RESET_TOKEN_CLEANUP_LIMIT)
    )
    db.query(PasswordResetToken).filter(
        PasswordResetToken.id.in_(stale_ids)
    ).delete(synchronize_session=False)

    latest = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .order_by(PasswordResetToken.created_at.desc())
        .first()
    )
    if latest is not None and latest.created_at >= now - RESET_REQUEST_COOLDOWN:
        return None

    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).update({PasswordResetToken.used_at: now}, synchronize_session=False)

    raw_token = secrets.token_urlsafe(32)
    reset = PasswordResetToken(
        user_id=user.id,
        token_hash=_token_hash(raw_token),
        expires_at=now + RESET_TOKEN_LIFETIME,
    )
    db.add(reset)
    db.commit()
    db.refresh(reset)

    separator = "&" if "?" in settings.PASSWORD_RESET_URL_BASE else "?"
    query = urlencode({"token": raw_token})
    reset_url = f"{settings.PASSWORD_RESET_URL_BASE}{separator}{query}"
    return PasswordResetDelivery(
        reset_id=reset.id,
        recipient=user.email,
        preferred_name=user.preferred_name,
        reset_url=reset_url,
    )


def confirm_password_reset(
    db: Session,
    token: str,
    new_password: str,
) -> PasswordChangedDelivery:
    now = datetime.now(timezone.utc)
    token_hash = _token_hash(token)
    reset_user_id = (
        db.query(PasswordResetToken.user_id)
        .filter(PasswordResetToken.token_hash == token_hash)
        .scalar()
    )
    if reset_user_id is None:
        raise InvalidPasswordResetToken()

    user = (
        db.query(User)
        .filter(User.id == reset_user_id)
        .with_for_update()
        .first()
    )
    reset = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == token_hash)
        .with_for_update()
        .first()
    )
    if reset is None or reset.used_at is not None or reset.expires_at <= now:
        raise InvalidPasswordResetToken()

    if user is None:
        raise InvalidPasswordResetToken()

    user.password_hash = hash_password(new_password)
    user.auth_version += 1
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).update({PasswordResetToken.used_at: now}, synchronize_session=False)
    db.add(user)
    db.commit()

    return PasswordChangedDelivery(
        reset_id=reset.id,
        recipient=user.email,
        preferred_name=user.preferred_name,
    )
