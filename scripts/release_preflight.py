#!/usr/bin/env python3
import json
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import engine
from app.core.rate_limit import force_storage
from app.core.settings import settings


def main() -> int:
    checks = {}
    expected_head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    try:
        with engine.connect() as connection:
            checks["database"] = connection.execute(text("SELECT 1")).scalar_one() == 1
            checks["migration"] = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == expected_head
            checks["pg_trgm"] = connection.execute(text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm')")).scalar_one()
    except Exception:
        checks.update(database=False, migration=False, pg_trgm=False)
    try:
        checks["rate_limit_storage"] = bool(force_storage.check())
    except Exception:
        checks["rate_limit_storage"] = False
    checks["environment"] = settings.APP_ENV in {"preview", "staging", "production", "prod"}
    print(json.dumps({"release": settings.RELEASE_VERSION, "checks": checks}, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
