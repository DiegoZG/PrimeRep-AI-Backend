import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    APP_ENV: str = os.getenv("APP_ENV", "local")
    RELEASE_VERSION: str = os.getenv("RELEASE_VERSION", "unknown")

    if APP_ENV.lower() not in {"local", "test", "preview", "staging", "production", "prod"}:
        raise RuntimeError("APP_ENV must be local, test, preview, staging, or production")

    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

    JWT_REFRESH_SECRET: str = os.getenv("JWT_REFRESH_SECRET", "")
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "30"))
    RATE_LIMIT_STORAGE_URI: str = os.getenv("RATE_LIMIT_STORAGE_URI", "memory://")
    PERSONAL_EXPORTS_PER_HOUR: int = int(os.getenv("PERSONAL_EXPORTS_PER_HOUR", "3"))
    RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
    EMAIL_FROM: str = os.getenv("EMAIL_FROM", "")
    EMAIL_OUTBOX_ENCRYPTION_KEY: str = os.getenv("EMAIL_OUTBOX_ENCRYPTION_KEY", "")
    PASSWORD_RESET_URL_BASE: str = os.getenv(
        "PASSWORD_RESET_URL_BASE", "http://localhost:8081/reset-password"
    )
    CORS_ALLOWED_ORIGINS: list[str] = [
        origin.strip() for origin in os.getenv(
            "CORS_ALLOWED_ORIGINS",
            "http://localhost:8081,http://127.0.0.1:8081",
        ).split(",") if origin.strip()
    ]

    # Optional — the exercise Q&A feature checks this at call time and returns
    # a 503 if missing, rather than failing the whole app at import time
    # (keeps local dev / tests working without an API key).
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_QA_MODEL: str = os.getenv("ANTHROPIC_QA_MODEL", "claude-haiku-4-5-20251001")
    EXPO_ACCESS_TOKEN: str = os.getenv("EXPO_ACCESS_TOKEN", "")

    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not set in .env")

    if not JWT_REFRESH_SECRET:
        raise RuntimeError("JWT_REFRESH_SECRET is not set in .env")

    if APP_ENV.lower() in {"production", "prod"} and RATE_LIMIT_STORAGE_URI.startswith("memory://"):
        raise RuntimeError("RATE_LIMIT_STORAGE_URI must use shared non-memory storage in production")

    if APP_ENV.lower() in {"production", "prod"}:
        if not EMAIL_OUTBOX_ENCRYPTION_KEY:
            raise RuntimeError("EMAIL_OUTBOX_ENCRYPTION_KEY is required in production")
        from cryptography.fernet import Fernet
        try:
            Fernet(EMAIL_OUTBOX_ENCRYPTION_KEY.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise RuntimeError("EMAIL_OUTBOX_ENCRYPTION_KEY must be a Fernet key") from exc
        if len(JWT_SECRET) < 32 or len(JWT_REFRESH_SECRET) < 32 or JWT_SECRET == JWT_REFRESH_SECRET:
            raise RuntimeError("Production JWT secrets must be distinct and at least 32 characters")
        if not RATE_LIMIT_STORAGE_URI.startswith(("redis://", "rediss://")):
            raise RuntimeError("Production rate limits require Redis storage")
        if not CORS_ALLOWED_ORIGINS or any(not origin.startswith("https://") for origin in CORS_ALLOWED_ORIGINS):
            raise RuntimeError("Production CORS origins must use HTTPS")
        missing_email_settings = [
            name
            for name, value in (
                ("RESEND_API_KEY", RESEND_API_KEY),
                ("EMAIL_FROM", EMAIL_FROM),
                ("PASSWORD_RESET_URL_BASE", PASSWORD_RESET_URL_BASE),
            )
            if not value
        ]
        if missing_email_settings:
            raise RuntimeError(
                "Password reset email settings are missing: "
                + ", ".join(missing_email_settings)
            )
        if not PASSWORD_RESET_URL_BASE.startswith("https://"):
            raise RuntimeError("PASSWORD_RESET_URL_BASE must use HTTPS in production")
        if "@resend.dev" in EMAIL_FROM.lower() or "@example.com" in EMAIL_FROM.lower():
            raise RuntimeError("Production EMAIL_FROM must use a verified sending domain")

settings = Settings()
