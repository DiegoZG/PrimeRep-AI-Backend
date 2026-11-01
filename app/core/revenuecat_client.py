import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.core.settings import settings


class RevenueCatUnavailable(Exception):
    pass


def configured() -> bool:
    return bool(settings.REVENUECAT_SECRET_API_KEY and settings.REVENUECAT_PRODUCT_IDS)


def fetch_customer(user_id: str) -> dict:
    if not configured():
        raise RevenueCatUnavailable("not_configured")
    request = Request(
        f"https://api.revenuecat.com/v1/subscribers/{quote(user_id, safe='')}",
        headers={"Authorization": f"Bearer {settings.REVENUECAT_SECRET_API_KEY}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            value = json.loads(response.read(1_048_577))
        if not isinstance(value, dict) or not isinstance(value.get("subscriber"), dict):
            raise ValueError("Invalid provider response")
        return value
    except HTTPError as error:
        if error.code == 404:
            return {"subscriber": {"entitlements": {}, "subscriptions": {}}}
        raise RevenueCatUnavailable("provider_unavailable") from error
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise RevenueCatUnavailable("provider_unavailable") from error


def erase_customer(user_id: str) -> None:
    if not configured():
        raise RevenueCatUnavailable("not_configured")
    request = Request(
        f"https://api.revenuecat.com/v1/subscribers/{quote(user_id, safe='')}",
        method="DELETE", headers={"Authorization": f"Bearer {settings.REVENUECAT_SECRET_API_KEY}"},
    )
    try:
        with urlopen(request, timeout=5):
            return
    except HTTPError as error:
        if error.code == 404:
            return
        raise RevenueCatUnavailable("provider_unavailable") from error
    except (URLError, TimeoutError, OSError) as error:
        raise RevenueCatUnavailable("provider_unavailable") from error
