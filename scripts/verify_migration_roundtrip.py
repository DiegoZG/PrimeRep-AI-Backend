#!/usr/bin/env python3
import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import DATABASE_URL
from app.core.settings import settings


def main() -> int:
    source = make_url(DATABASE_URL)
    if source.host not in {"localhost", "127.0.0.1"} or settings.APP_ENV not in {"local", "test"}:
        raise RuntimeError("Migration round trip is restricted to local PostgreSQL")
    database_name = f"primerep_part9_migration_{uuid.uuid4().hex[:12]}"
    admin = create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
    migration_url = source.set(database=database_name).render_as_string(hide_password=False)
    environment = {**os.environ, "DATABASE_URL": migration_url, "APP_ENV": "test"}
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
            created = True
        commands = ["upgrade", "downgrade", "upgrade"]
        revisions = ["head", "-1", "head"]
        for command, revision in zip(commands, revisions):
            subprocess.run([sys.executable, "-m", "alembic", command, revision], check=True, env=environment)
        print("Migration upgrade/downgrade/upgrade passed on disposable local database")
        return 0
    finally:
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database_name}"'))
        admin.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
