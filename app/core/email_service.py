import html
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from app.core.settings import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailMessage:
    recipient: str
    subject: str
    html: str
    idempotency_key: str


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> None: ...


class DisabledEmailSender:
    def send(self, message: EmailMessage) -> None:
        raise RuntimeError("Email delivery is not configured")


class CaptureEmailSender:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.messages.append(message)


class ResendEmailSender:
    def __init__(self, api_key: str, sender: str, resend_module: Any = None) -> None:
        if resend_module is None:
            import resend as resend_module

        self._resend = resend_module
        self._resend.api_key = api_key
        self._sender = sender

    def send(self, message: EmailMessage) -> None:
        params = {
            "from": self._sender,
            "to": [message.recipient],
            "subject": message.subject,
            "html": message.html,
        }
        options = {"idempotency_key": message.idempotency_key}
        self._resend.Emails.send(params, options)


@lru_cache(maxsize=1)
def get_email_sender() -> EmailSender:
    if settings.RESEND_API_KEY and settings.EMAIL_FROM:
        return ResendEmailSender(settings.RESEND_API_KEY, settings.EMAIL_FROM)
    return DisabledEmailSender()


def deliver_email(sender: EmailSender, message: EmailMessage) -> None:
    try:
        sender.send(message)
    except Exception:
        logger.exception(
            "Email delivery failed",
            extra={"email_event": message.idempotency_key.split("/", 1)[0]},
        )


def password_reset_email(
    recipient: str,
    preferred_name: str,
    reset_url: str,
    reset_id: str,
) -> EmailMessage:
    safe_name = html.escape(preferred_name)
    safe_url = html.escape(reset_url, quote=True)
    return EmailMessage(
        recipient=recipient,
        subject="Reset your PrimeRep password",
        html=(
            f"<p>Hi {safe_name},</p>"
            "<p>Use the link below to reset your PrimeRep password. "
            "It expires in 30 minutes and can only be used once.</p>"
            f'<p><a href="{safe_url}">Reset password</a></p>'
            "<p>If you did not request this, you can ignore this email.</p>"
        ),
        idempotency_key=f"password-reset/{reset_id}",
    )


def password_changed_email(
    recipient: str,
    preferred_name: str,
    reset_id: str,
) -> EmailMessage:
    safe_name = html.escape(preferred_name)
    return EmailMessage(
        recipient=recipient,
        subject="Your PrimeRep password was changed",
        html=(
            f"<p>Hi {safe_name},</p>"
            "<p>Your PrimeRep password was changed successfully. "
            "You have been signed out on all devices.</p>"
            "<p>If you did not make this change, contact PrimeRep support immediately.</p>"
        ),
        idempotency_key=f"password-changed/{reset_id}",
    )
