from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.core.settings import settings
from app.core.security.jwt import decode_access_token
from jose import JWTError
from fastapi import HTTPException
from limits import parse
from limits.storage import storage_from_string
from limits.strategies import FixedWindowRateLimiter


def user_or_ip_key(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        try:
            user_id = decode_access_token(authorization[7:]).get("sub")
            if user_id:
                return str(user_id)
        except JWTError:
            pass
    return get_remote_address(request)


limiter = Limiter(key_func=get_remote_address, storage_uri=settings.RATE_LIMIT_STORAGE_URI)
user_limiter = Limiter(key_func=user_or_ip_key, storage_uri=settings.RATE_LIMIT_STORAGE_URI)

force_storage = storage_from_string(settings.RATE_LIMIT_STORAGE_URI)
force_limiter = FixedWindowRateLimiter(force_storage)


def check_force_regeneration(user_id: str) -> None:
    """Shared storage-backed force-only limiter used by both workout endpoints."""
    if not force_limiter.hit(parse("3/minute"), "force-regeneration", user_id):
        raise HTTPException(status_code=429, detail="Too many forced regenerations. Try again shortly.")


def reset_rate_limits() -> None:
    limiter.reset()
    user_limiter.reset()
    force_storage.reset()
