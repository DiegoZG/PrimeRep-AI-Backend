import ast
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from migration_helpers import ensure_feature_flags, ensure_flag_targeting, ensure_session_arrangements

ROOT = Path(__file__).resolve().parents[1]


def test_revision_graph_has_unique_ids_and_one_head():
    ids = []
    for path in (ROOT / "alembic/versions").glob("*.py"):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "revision" for target in node.targets):
                ids.append(ast.literal_eval(node.value))
    assert len(ids) == len(set(ids))
    assert ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads() == ["e6f7g8h9i0j1"]


@pytest.fixture
def migration_database(monkeypatch):
    base = os.getenv("SUBSCRIPTION_MIGRATION_TEST_URL")
    if not base:
        pytest.skip("Set SUBSCRIPTION_MIGRATION_TEST_URL for isolated PostgreSQL migration snapshots")
    engine = create_engine(base, isolation_level="AUTOCOMMIT")
    database = f"billing_migration_{uuid4().hex}"
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database}"'))
    url = make_url(base).set(database=database)
    target_url = url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", target_url)
    config = Config(str(ROOT / "alembic.ini"))
    target = create_engine(target_url)
    try:
        yield config, target
    finally:
        target.dispose()
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database}"'))
        engine.dispose()


@pytest.mark.parametrize("legacy", ["fresh", "a2_flags", "a2_arrangements", "b3_flags", "b3_combined"])
def test_fresh_and_legacy_stamps_upgrade_without_losing_data(migration_database, legacy):
    config, engine = migration_database
    if legacy == "fresh":
        command.upgrade(config, "head")
    else:
        command.upgrade(config, "z1a2b3c4d5e6")
        with engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            if legacy in {"a2_arrangements", "b3_combined"}:
                ensure_session_arrangements(operations)
            if legacy in {"a2_flags", "b3_flags", "b3_combined"}:
                ensure_feature_flags(operations)
                connection.execute(text("INSERT INTO feature_flag_configs (environment, version, subscriptions_purchases_enabled, subscriptions_enforcement_enabled, launch_locked, updated_at) VALUES ('migration-test', 7, false, false, false, now())"))
            if legacy in {"b3_flags", "b3_combined"}:
                ensure_flag_targeting(operations)
            connection.execute(text("INSERT INTO users (id, email, preferred_name, password_hash, has_completed_onboarding, subscription_tier, coach_insights_enabled) VALUES ('migration-sentinel', 'migration@example.test', 'Preserve', 'test', false, 'free', false)"))
        command.stamp(config, "b3c4d5e6f7g8" if legacy.startswith("b3") else "a2b3c4d5e6f7")
        command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        schema = inspect(connection)
        assert {"effective_workout", "arrangement_revision"} <= {column["name"] for column in schema.get_columns("workout_sessions")}
        assert "payload_fingerprint" in {column["name"] for column in schema.get_columns("workout_session_operations")}
        assert {"feature_flag_configs", "feature_flag_audits", "feature_flag_user_overrides", "subscription_states", "subscription_transition_grants", "billing_webhook_events", "billing_jobs"} <= set(schema.get_table_names())
        assert {"subscriptions_purchases_audience", "subscriptions_enforcement_audience"} <= {column["name"] for column in schema.get_columns("feature_flag_configs")}
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "e6f7g8h9i0j1"
        if legacy != "fresh":
            assert connection.scalar(text("SELECT preferred_name FROM users WHERE id = 'migration-sentinel'")) == "Preserve"
        if legacy in {"a2_flags", "b3_flags", "b3_combined"}:
            assert connection.scalar(text("SELECT version FROM feature_flag_configs WHERE environment = 'migration-test'")) == 7
            assert connection.scalar(text("SELECT subscriptions_purchases_enabled FROM feature_flag_configs WHERE environment = 'migration-test'")) is False
        operations = Operations(MigrationContext.configure(connection))
        ensure_session_arrangements(operations)
        ensure_feature_flags(operations)
        ensure_flag_targeting(operations)
