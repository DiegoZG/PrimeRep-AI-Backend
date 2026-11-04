"""Process a bounded billing batch; schedule every minute in each environment."""
import argparse

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.feature_flag_service import flag_environment
from app.core.subscription_service import process_billing_job, prune_billing_history, schedule_due_reconciliations
from app.models.subscription import BillingJob


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--retry-failed", action="store_true")
    arguments = parser.parse_args()
    if not 1 <= arguments.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    with SessionLocal() as db:
        if arguments.retry_failed:
            jobs = db.scalars(select(BillingJob).where(BillingJob.environment == flag_environment(), BillingJob.status == "failed").limit(arguments.limit).with_for_update(skip_locked=True)).all()
            for job in jobs:
                job.status, job.attempts = "pending", 0
        schedule_due_reconciliations(db, limit=arguments.limit)
        prune_billing_history(db)
        db.commit()
    processed = 0
    for _ in range(arguments.limit):
        with SessionLocal() as db:
            if not process_billing_job(db):
                break
            db.commit()
            processed += 1
    print(f"Processed {processed} billing jobs.")


if __name__ == "__main__":
    main()
