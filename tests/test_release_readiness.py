import os
import logging
import subprocess
import sys

from fastapi.testclient import TestClient

from app.main import app


def test_health_includes_request_id_without_logging_query(caplog):
    with caplog.at_level(logging.INFO, logger="primerep.request"):
        response = TestClient(app).get("/health?email=secret@example.com")
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert "secret@example.com" not in caplog.text
    assert any(getattr(record, "endpoint", None) == "/health" for record in caplog.records)


def test_production_rejects_missing_email_encryption_key():
    env = dict(os.environ)
    env.update({
        "APP_ENV": "production",
        "JWT_SECRET": "a" * 40,
        "JWT_REFRESH_SECRET": "b" * 40,
        "RATE_LIMIT_STORAGE_URI": "redis://localhost:6379/0",
        "CORS_ALLOWED_ORIGINS": "https://app.example.test",
        "RESEND_API_KEY": "test-key",
        "EMAIL_FROM": "PrimeRep <support@primerep.test>",
        "PASSWORD_RESET_URL_BASE": "https://app.example.test/reset-password",
        "EMAIL_OUTBOX_ENCRYPTION_KEY": "",
    })
    result = subprocess.run(
        [sys.executable, "-c", "from app.core.settings import settings"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "EMAIL_OUTBOX_ENCRYPTION_KEY is required" in result.stderr
