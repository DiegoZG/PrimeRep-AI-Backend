"""Run daily to remove daily health summaries beyond the 90-day retention window."""

from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.core.health_service import purge_expired_days


if __name__ == "__main__":
    with SessionLocal() as db:
        count = purge_expired_days(db, datetime.now(timezone.utc))
    print(f"Expired health summaries removed: {count}")
