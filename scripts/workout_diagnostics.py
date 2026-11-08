"""Read observed workout diagnostics or prune expired records for this environment."""
import argparse
import json

from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.workout_diagnostics_report import diagnostic_report
from app.core.workout_diagnostics_service import prune_diagnostics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("report", "prune"))
    parser.add_argument("--days", type=int, default=7)
    arguments = parser.parse_args()
    if not 1 <= arguments.days <= 90:
        parser.error("--days must be between 1 and 90")
    with SessionLocal() as db:
        if arguments.command == "prune":
            result = prune_diagnostics(db)
            db.commit()
        else:
            if db.get_bind().dialect.name == "postgresql":
                db.execute(text("SET TRANSACTION READ ONLY"))
            result = diagnostic_report(db, days=arguments.days)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
