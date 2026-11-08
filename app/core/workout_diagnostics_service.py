from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.feature_flag_service import flag_environment, get_feature_flags
from app.models.workout_diagnostics import WorkoutDiagnostic, WorkoutDiagnosticAggregate
from app.schemas.workout_diagnostics import WorkoutDiagnosticBatch

RAW_DAYS = 14
AGGREGATE_DAYS = 90
DIMENSIONS = (
    "environment", "bucket_date", "platform", "app_version", "event_type", "operation_type", "outcome",
    "error_category", "acknowledgment_kind", "recovered_after_restart", "finish_stage", "metric", "histogram_bucket",
)
DURATION_BUCKETS = (10, 50, 100, 250, 1000, 5000, 30000, 300000, 3600000, 86400000)
AGE_BUCKETS = (1000, 5000, 30000, 120000, 600000, 3600000, 86400000, 604800000, 31536000000)
EXPORT_FIELDS = (
    "event_id", "event_type", "occurred_at_ms", "trace_id", "operation_type", "outcome", "error_category", "http_status",
    "duration_ms", "queue_age_ms", "queue_depth", "unknown_age_count", "saved_count", "dropped_count", "retry_delay_ms",
    "request_attempt", "acknowledgment_kind", "recovered_after_restart", "player_active", "clock_invalid", "finish_stage",
)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _insert(db, model):
    return (sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert)(model)


def _bucket(value, bounds):
    return str(next(bound for bound in bounds if value <= bound))


def _measurements(payload):
    yield "events", "", 0
    kind = payload["event_type"]
    if payload.get("clock_invalid"):
        yield "clock_invalid", "", 0
    if kind in {"local_save", "sync_result", "finish_result"} and payload.get("duration_ms") is not None:
        value = payload["duration_ms"]
        yield "duration_ms", _bucket(value, DURATION_BUCKETS), value
    if kind == "local_save" and payload["outcome"] == "success":
        yield "saved_count", "", payload["saved_count"]
    if kind == "operation_terminal" and payload["outcome"] == "acknowledged":
        value = payload.get("queue_age_ms")
        if value is None:
            yield "unknown_queue_age", "", 0
        else:
            yield "queue_age_ms", _bucket(value, AGE_BUCKETS), value
    if kind == "retry_scheduled" and payload.get("retry_delay_ms") is not None:
        value = payload["retry_delay_ms"]
        yield "retry_delay_ms", _bucket(value, DURATION_BUCKETS), value
    if kind == "diagnostics_dropped":
        yield "dropped_count", "", payload["dropped_count"]


def ingest_diagnostics(db: Session, user_id: str, batch: WorkoutDiagnosticBatch, *, now=None):
    now = utc(now or datetime.now(timezone.utc))
    environment = flag_environment()
    if not get_feature_flags(db, user_id)["flags"]["workout_reliability_monitoring_enabled"]:
        return {"enabled": False, "accepted": 0, "duplicates": 0, "discarded": len(batch.events)}
    db.execute(delete(WorkoutDiagnostic).where(
        WorkoutDiagnostic.environment == environment, WorkoutDiagnostic.user_id == user_id,
        WorkoutDiagnostic.received_at < now - timedelta(days=RAW_DAYS),
    ))
    accepted = 0
    aggregates = defaultdict(lambda: [0, 0])
    for event in sorted(batch.events, key=lambda item: (str(item.trace_id or item.event_id), str(item.event_id))):
        payload = event.model_dump(mode="json", exclude_none=True)
        event_ms = payload["occurred_at_ms"]
        received_ms = int(now.timestamp() * 1000)
        if payload.get("clock_invalid") or event_ms < received_ms - 7 * 86400000 or event_ms > received_ms + 300000:
            payload["clock_invalid"] = True
            payload.pop("queue_age_ms", None)
        statement = _insert(db, WorkoutDiagnostic).values(
            environment=environment, user_id=user_id, event_id=str(event.event_id), trace_id=str(event.trace_id) if event.trace_id else None,
            event_type=event.event_type, received_at=now, platform=batch.platform, app_version=batch.app_version, payload=payload,
        ).on_conflict_do_nothing().returning(WorkoutDiagnostic.event_id)
        if db.scalar(statement) is None:
            continue
        accepted += 1
        dimensions = {
            "environment": environment, "bucket_date": now.date(), "platform": batch.platform, "app_version": batch.app_version,
            **{name: str(payload.get(name, "")) for name in DIMENSIONS[4:11]},
        }
        recovered = payload.get("recovered_after_restart")
        dimensions["recovered_after_restart"] = "unknown" if recovered is None else str(recovered).lower()
        for metric, bucket, total in _measurements(payload):
            values = {**dimensions, "metric": metric, "histogram_bucket": bucket}
            counts = aggregates[tuple(values[name] for name in DIMENSIONS)]
            counts[0] += 1
            counts[1] += total
    for key, (count, total) in sorted(aggregates.items()):
        values = dict(zip(DIMENSIONS, key))
        statement = _insert(db, WorkoutDiagnosticAggregate).values(**values, sample_count=count, total=total)
        db.execute(statement.on_conflict_do_update(index_elements=list(DIMENSIONS), set_={
            "sample_count": WorkoutDiagnosticAggregate.sample_count + statement.excluded.sample_count,
            "total": WorkoutDiagnosticAggregate.total + statement.excluded.total,
        }))
    return {"enabled": True, "accepted": accepted, "duplicates": len(batch.events) - accepted, "discarded": 0}


def prune_diagnostics(db: Session, *, now=None):
    now = utc(now or datetime.now(timezone.utc))
    environment = flag_environment()
    raw = db.execute(delete(WorkoutDiagnostic).where(
        WorkoutDiagnostic.environment == environment, WorkoutDiagnostic.received_at < now - timedelta(days=RAW_DAYS),
    )).rowcount
    aggregates = db.execute(delete(WorkoutDiagnosticAggregate).where(
        WorkoutDiagnosticAggregate.environment == environment,
        WorkoutDiagnosticAggregate.bucket_date < now.date() - timedelta(days=AGGREGATE_DAYS - 1),
    )).rowcount
    return {"raw_deleted": raw, "aggregate_buckets_deleted": aggregates}


def export_diagnostics(db: Session, user_id: str):
    rows = db.query(WorkoutDiagnostic).filter(
        WorkoutDiagnostic.user_id == user_id,
        WorkoutDiagnostic.received_at >= datetime.now(timezone.utc) - timedelta(days=RAW_DAYS),
    ).order_by(WorkoutDiagnostic.environment, WorkoutDiagnostic.received_at, WorkoutDiagnostic.event_id).yield_per(250)
    for row in rows:
        yield {
            "environment": row.environment, "received_at": utc(row.received_at).isoformat(),
            "schema_version": 1, "platform": row.platform, "app_version": row.app_version,
            **{name: row.payload[name] for name in EXPORT_FIELDS if name in row.payload},
        }
