import logging
from types import SimpleNamespace

import pytest

from app.core import ai_provider
from app.core.ai_budget_service import AIBudgetUnavailable, price_micro_usd
from app.core.settings import settings
from app.schemas.equipment_scan import EquipmentSummary


def test_only_verified_pricing_is_allowed_and_reasoning_is_not_double_counted():
    assert price_micro_usd("gpt-6-luna", 1000, 100) == 175
    assert price_micro_usd("gpt-6-luna", 1_050_000, 768) == 263076
    for model, incoming, outgoing in [("unknown", 100, 10), ("gpt-6-luna", True, 10), ("gpt-6-luna", -1, 0)]:
        with pytest.raises(AIBudgetUnavailable):
            price_micro_usd(model, incoming, outgoing)


def test_text_adapter_uses_bounded_stateless_no_tool_responses_and_preserves_usage(monkeypatch):
    calls, settlements = [], []
    usage = SimpleNamespace(input_tokens=100, output_tokens=20, output_tokens_details=SimpleNamespace(reasoning_tokens=0))
    class API:
        responses = SimpleNamespace(create=lambda **kwargs: calls.append(kwargs) or SimpleNamespace(status="completed", output_text="  Answer  ", usage=usage))
        def __enter__(self): return self
        def __exit__(self, *args): return None
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "not-a-live-key")
    monkeypatch.setattr(ai_provider, "client", lambda timeout: API())
    monkeypatch.setattr(ai_provider.budget, "reserve", lambda *args, **kwargs: "owned-reservation")
    monkeypatch.setattr(ai_provider.budget, "settle", lambda *args, **kwargs: settlements.append((args, kwargs)))
    assert ai_provider.call_text("safe scope", "question", purpose="exercise_qa").text == "Answer"
    assert calls[0]["store"] is False and calls[0]["tools"] == []
    assert calls[0]["reasoning"] == {"effort": "none"}
    assert calls[0]["max_output_tokens"] == 500 and calls[0]["service_tier"] == "default"
    assert settlements[0][0] == ("owned-reservation", usage)


def test_response_failure_retains_unknown_reservation_and_never_retries(monkeypatch):
    calls, settlements = [], []
    def failed(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("PRIVATE_RAW_PAYLOAD")
    class API:
        responses = SimpleNamespace(create=failed)
        def __enter__(self): return self
        def __exit__(self, *args): return None
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "not-a-live-key")
    monkeypatch.setattr(ai_provider, "client", lambda timeout: API())
    monkeypatch.setattr(ai_provider.budget, "reserve", lambda *args, **kwargs: "reservation")
    monkeypatch.setattr(ai_provider.budget, "settle", lambda *args, **kwargs: settlements.append(args))
    with pytest.raises(ai_provider.AIUnavailable) as error:
        ai_provider.call_text("scope", "question", purpose="exercise_qa")
    assert "PRIVATE" not in str(error.value)
    assert len(calls) == 1 and settlements == [("reservation",)]


def test_unconfigured_unpriced_or_oversized_text_stops_before_api(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_provider, "client", lambda *args: calls.append("api"))
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    with pytest.raises(ai_provider.AIUnavailable):
        ai_provider.call_text("scope", "question", purpose="exercise_qa")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "not-a-live-key")
    with pytest.raises(ai_provider.AIUnavailable):
        ai_provider.call_text("scope", "x" * 70_000, purpose="exercise_qa")
    assert not calls


def test_sdk_retries_disabled_and_raw_debug_canaries_cannot_escape(monkeypatch, caplog):
    import openai
    options = []
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "SECRET_KEY_CANARY")
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: options.append(kwargs) or object())
    caplog.set_level(logging.DEBUG)
    ai_provider.client(7)
    logging.getLogger("openai._base_client").debug("IMAGE_BASE64_CANARY QUESTION_CANARY SECRET_KEY_CANARY")
    logging.getLogger("httpcore.http11").debug("SECRET_KEY_CANARY")
    assert options[0]["max_retries"] == 0 and options[0]["timeout"] == 7
    assert options[0]["base_url"] == "https://api.openai.com/v1"
    assert not any(marker in caplog.text for marker in ["IMAGE_BASE64_CANARY", "QUESTION_CANARY", "SECRET_KEY_CANARY"])
