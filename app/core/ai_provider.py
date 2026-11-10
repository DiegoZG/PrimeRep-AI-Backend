"""Bounded OpenAI Responses access shared by coaching, drafts and scanning."""
from dataclasses import dataclass
from typing import Any

from app.core import ai_budget_service as budget
from app.core.settings import settings


class AIUnavailable(Exception):
    pass


@dataclass(frozen=True)
class AIResponse:
    text: str
    parsed: Any = None


def configured():
    return bool(settings.OPENAI_API_KEY)


def quiet_provider_logging():
    import logging
    for prefix in ("openai", "httpx", "httpcore"):
        names = [prefix, *[name for name in logging.Logger.manager.loggerDict if name.startswith(prefix + ".")]]
        for name in names:
            logger = logging.getLogger(name)
            logger.setLevel(logging.CRITICAL + 1)
            logger.disabled = True
            logger.propagate = False
            logger.handlers.clear()


def client(timeout=10.0):
    if not configured():
        raise AIUnavailable("AI is not configured")
    from openai import OpenAI
    quiet_provider_logging()
    return OpenAI(api_key=settings.OPENAI_API_KEY, base_url="https://api.openai.com/v1", max_retries=0, timeout=timeout)


def call_text(system, user_message, *, purpose, max_output_tokens=500, response_type=None, timeout=10.0):
    if not configured() or settings.OPENAI_CHAT_REASONING not in {"none", "low"}:
        raise AIUnavailable("AI is unavailable")
    if not isinstance(system, str) or not isinstance(user_message, str):
        raise AIUnavailable("AI input is unavailable")
    import json
    schema_bytes = len(json.dumps(response_type.model_json_schema()).encode()) if response_type else 0
    maximum_input = len(system.encode()) + len(user_message.encode()) + schema_bytes + 2048
    if maximum_input > 65_536 or not 1 <= max_output_tokens <= 3000:
        raise AIUnavailable("AI input exceeds its bounded request")
    reservation = None
    try:
        reservation = budget.reserve(purpose, settings.OPENAI_CHAT_MODEL, maximum_input, max_output_tokens)
        options = dict(model=settings.OPENAI_CHAT_MODEL, instructions=system, input=user_message,
                       max_output_tokens=max_output_tokens, reasoning={"effort": settings.OPENAI_CHAT_REASONING},
                       store=False, service_tier="default", tools=[], timeout=timeout)
        with client(timeout) as api:
            response = api.responses.parse(text_format=response_type, **options) if response_type else api.responses.create(**options)
        budget.settle(reservation, getattr(response, "usage", None))
        if getattr(response, "status", None) != "completed":
            raise AIUnavailable("AI response is unavailable")
        text_value = getattr(response, "output_text", "").strip()
        parsed = getattr(response, "output_parsed", None)
        if response_type is not None and not isinstance(parsed, response_type):
            raise AIUnavailable("AI response is unavailable")
        if response_type is None and (not text_value or len(text_value) > 20_000):
            raise AIUnavailable("AI response is unavailable")
        return AIResponse(text=text_value, parsed=parsed)
    except Exception:
        if reservation is not None:
            budget.settle(reservation)
        raise AIUnavailable("AI is temporarily unavailable") from None
