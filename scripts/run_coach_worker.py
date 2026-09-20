#!/usr/bin/env python3
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.core.coach_notification_service import run_worker_once
from app.core.database import SessionLocal


def main() -> int:
    with SessionLocal() as db:
        result = run_worker_once(db)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
