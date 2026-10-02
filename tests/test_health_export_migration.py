import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.core.database import engine


def test_legacy_export_flags_require_fresh_consent_after_migration(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/z1a2b3c4d5e6_normalize_unverified_health_export.py"
    spec = importlib.util.spec_from_file_location("health_export_consent_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("""
                CREATE TEMP TABLE health_sources (
                    user_id TEXT, source TEXT, connection_revision INTEGER,
                    enabled BOOLEAN, read_enabled BOOLEAN, export_enabled BOOLEAN,
                    export_enabled_at TIMESTAMPTZ, disconnected_at TIMESTAMPTZ,
                    last_successful_sync_at TIMESTAMPTZ
                ) ON COMMIT DROP
            """))
            connection.execute(text("""
                CREATE TEMP TABLE health_days (user_id TEXT, source TEXT, local_date DATE) ON COMMIT DROP
            """))
            connection.execute(text("""
                CREATE TEMP TABLE health_preferences (user_id TEXT, primary_source TEXT, coach_enabled BOOLEAN) ON COMMIT DROP
            """))
            connection.execute(text("""
                INSERT INTO health_sources
                    (user_id, source, connection_revision, enabled, read_enabled, export_enabled,
                     export_enabled_at, last_successful_sync_at)
                VALUES
                    ('read-legacy', 'apple_health', 2, TRUE, TRUE, TRUE, NULL, CURRENT_TIMESTAMP),
                    ('export-legacy', 'apple_health', 4, TRUE, FALSE, TRUE, NULL, CURRENT_TIMESTAMP),
                    ('read-only', 'apple_health', 7, TRUE, TRUE, FALSE, NULL, CURRENT_TIMESTAMP),
                    ('verified', 'apple_health', 9, TRUE, TRUE, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """))
            connection.execute(text("""
                INSERT INTO health_days VALUES
                    ('read-legacy', 'apple_health', CURRENT_DATE),
                    ('export-legacy', 'apple_health', CURRENT_DATE)
            """))
            connection.execute(text("""
                INSERT INTO health_preferences VALUES
                    ('read-legacy', 'apple_health', TRUE),
                    ('export-legacy', 'apple_health', TRUE)
            """))

            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
            migration.upgrade()
            rows = {
                row.user_id: row for row in connection.execute(text("""
                    SELECT user_id, connection_revision, enabled, read_enabled, export_enabled,
                           export_enabled_at, disconnected_at, last_successful_sync_at
                    FROM health_sources
                """)).all()
            }
            assert (rows["read-legacy"].connection_revision, rows["read-legacy"].enabled,
                    rows["read-legacy"].read_enabled, rows["read-legacy"].export_enabled,
                    rows["read-legacy"].export_enabled_at) == (3, True, True, False, None)
            assert rows["read-legacy"].last_successful_sync_at is not None
            assert (rows["export-legacy"].connection_revision, rows["export-legacy"].enabled,
                    rows["export-legacy"].export_enabled, rows["export-legacy"].export_enabled_at) == (5, False, False, None)
            assert rows["export-legacy"].disconnected_at is not None
            assert rows["export-legacy"].last_successful_sync_at is None
            assert rows["read-only"].connection_revision == 7
            assert rows["verified"].connection_revision == 9
            assert rows["verified"].export_enabled is True
            assert rows["verified"].export_enabled_at is not None
            assert connection.execute(text("SELECT user_id FROM health_days")).scalars().all() == ["read-legacy"]
            preferences = {
                row.user_id: row for row in connection.execute(text("SELECT * FROM health_preferences")).all()
            }
            assert (preferences["read-legacy"].primary_source, preferences["read-legacy"].coach_enabled) == ("apple_health", True)
            assert (preferences["export-legacy"].primary_source, preferences["export-legacy"].coach_enabled) == (None, False)

            migration.downgrade()
            migration.upgrade()
            revisions = dict(connection.execute(text("SELECT user_id, connection_revision FROM health_sources")).all())
            assert revisions == {"read-legacy": 3, "export-legacy": 5, "read-only": 7, "verified": 9}
        finally:
            transaction.rollback()
