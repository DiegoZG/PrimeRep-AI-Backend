import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    APP_ENV: str = os.getenv("APP_ENV", "local")

    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

    JWT_REFRESH_SECRET: str = os.getenv("JWT_REFRESH_SECRET", "")
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "30"))
    RATE_LIMIT_STORAGE_URI: str = os.getenv("RATE_LIMIT_STORAGE_URI", "memory://")
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

    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not set in .env")

    if not JWT_REFRESH_SECRET:
        raise RuntimeError("JWT_REFRESH_SECRET is not set in .env")

    if APP_ENV.lower() in {"production", "prod"} and RATE_LIMIT_STORAGE_URI.startswith("memory://"):
        raise RuntimeError("RATE_LIMIT_STORAGE_URI must use shared non-memory storage in production")

settings = Settings()
