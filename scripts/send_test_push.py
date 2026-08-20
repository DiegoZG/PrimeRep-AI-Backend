#!/usr/bin/env python3
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.core.database import SessionLocal
from app.models.push_token import PushToken
from app.models.user import User


PUSH_URL = "https://exp.host/--/api/v2/push/send"
RECEIPTS_URL = "https://exp.host/--/api/v2/push/getReceipts"


def _post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Expo request failed: {error}") from error


def _latest_token(email: str) -> str:
    with SessionLocal() as db:
        row = (
            db.query(PushToken)
            .join(User, User.id == PushToken.user_id)
            .filter(User.email == email)
            .order_by(PushToken.updated_at.desc())
            .first()
        )
    if row is None:
        raise RuntimeError(f"No push token found for {email}")
    return row.token


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a PrimeRep test push")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--email")
    target.add_argument("--token")
    parser.add_argument("--title", default="PrimeRep test")
    parser.add_argument("--body", default="Push notifications are working.")
    parser.add_argument("--check-receipt", action="store_true")
    parser.add_argument("--receipt-delay", type=float, default=5.0)
    parser.add_argument("--receipt-attempts", type=int, default=4)
    parser.add_argument("--receipt-backoff", type=float, default=2.0)
    args = parser.parse_args()

    try:
        token = args.token or _latest_token(args.email)
        response = _post_json(
            PUSH_URL,
            {"to": token, "sound": "default", "title": args.title, "body": args.body},
        )
        print(json.dumps(response, indent=2))
        ticket = response.get("data", {})
        if ticket.get("status") != "ok":
            raise RuntimeError(f"Expo rejected the push ticket: {ticket}")

        if args.check_receipt:
            ticket_id = ticket.get("id")
            if not ticket_id:
                raise RuntimeError("Expo did not return a receipt ID")
            attempts = max(1, args.receipt_attempts)
            delay = max(0, args.receipt_delay)
            for attempt in range(attempts):
                if delay:
                    time.sleep(delay)
                receipt_response = _post_json(RECEIPTS_URL, {"ids": [ticket_id]})
                print(json.dumps(receipt_response, indent=2))
                receipt = receipt_response.get("data", {}).get(ticket_id)
                if receipt:
                    if receipt.get("status") != "ok":
                        raise RuntimeError(f"Expo receipt failed: {receipt}")
                    break
                if attempt == attempts - 1:
                    raise RuntimeError(
                        f"Expo receipt was not available after {attempts} attempts"
                    )
                delay = max(0, args.receipt_backoff) * (2**attempt)
    except Exception as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
