#!/usr/bin/env python3
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal
from app.core.email_outbox_service import run_worker_once


if __name__ == "__main__":
    with SessionLocal() as db:
        print(json.dumps(run_worker_once(db), sort_keys=True))
