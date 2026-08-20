import sys

from scripts import send_test_push


def test_receipt_polling_retries_until_available(monkeypatch):
    responses = iter(
        [
            {"data": {"status": "ok", "id": "ticket-1"}},
            {"data": {}},
            {"data": {}},
            {"data": {"ticket-1": {"status": "ok"}}},
        ]
    )
    monkeypatch.setattr(send_test_push, "_post_json", lambda *_: next(responses))
    monkeypatch.setattr(send_test_push.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "send_test_push.py",
            "--token",
            "ExponentPushToken[test]",
            "--check-receipt",
            "--receipt-attempts",
            "3",
            "--receipt-delay",
            "0",
            "--receipt-backoff",
            "0",
        ],
    )

    assert send_test_push.main() == 0


def test_receipt_polling_fails_only_after_attempts_are_exhausted(
    monkeypatch, capsys
):
    calls = 0

    def post_json(*_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"data": {"status": "ok", "id": "ticket-2"}}
        return {"data": {}}

    monkeypatch.setattr(send_test_push, "_post_json", post_json)
    monkeypatch.setattr(send_test_push.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "send_test_push.py",
            "--token",
            "ExponentPushToken[test]",
            "--check-receipt",
            "--receipt-attempts",
            "2",
            "--receipt-delay",
            "0",
            "--receipt-backoff",
            "0",
        ],
    )

    assert send_test_push.main() == 1
    assert calls == 3
    assert "not available after 2 attempts" in capsys.readouterr().err
