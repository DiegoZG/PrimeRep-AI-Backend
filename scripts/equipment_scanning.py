"""Internal receipt retention and aggregate budget readout; never transmits photos."""
import argparse
import json

from sqlalchemy import func

from app.core.database import SessionLocal
from app.core.equipment_scan_service import prune
from app.core.feature_flag_service import flag_environment
from app.models.equipment_scan import AIRequestReservation, AISpendPeriod


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prune", "budget"])
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.action == "prune":
            attempts, quotas = prune(db)
            print(json.dumps({"expired_attempts": attempts, "expired_quotas": quotas}))
        else:
            environment = flag_environment()
            periods = db.query(AISpendPeriod).filter_by(environment=environment).order_by(AISpendPeriod.period).all()
            unknown = db.query(func.count()).select_from(AIRequestReservation).filter(AIRequestReservation.environment == environment, AIRequestReservation.status.in_(["unknown", "reserved"])).scalar()
            print(json.dumps({"periods": [{"period": row.period, "committed_micro_usd": row.committed_micro_usd} for row in periods], "unsettled_reservations": unknown}))


if __name__ == "__main__":
    main()
