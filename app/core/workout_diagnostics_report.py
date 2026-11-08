from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import BigInteger, and_, cast, func, select
from sqlalchemy.orm import Session

from app.core.feature_flag_service import flag_environment
from app.core.workout_diagnostics_service import AGGREGATE_DAYS, RAW_DAYS, utc
from app.models.workout_diagnostics import WorkoutDiagnostic as Raw, WorkoutDiagnosticAggregate as Aggregate


def _distribution(rows, metric, **filters):
    buckets = Counter()
    total = 0
    for row in rows:
        if row.metric == metric and all(getattr(row, key) == value for key, value in filters.items()):
            buckets[int(row.histogram_bucket)] += row.sample_count
            total += row.total
    count = sum(buckets.values())
    return {
        "known_samples": count, "mean_ms": round(total / count, 2) if count else None,
        "histogram": [{"upper_bound_ms": bound, "samples": buckets[bound]} for bound in sorted(buckets)],
    }


def _cohorts(db, environment, cutoff, raw_cutoff):
    def traces(kind, start):
        return select(Raw.user_id, Raw.trace_id, Raw.payload).where(
            Raw.environment == environment, Raw.event_type == kind, Raw.received_at >= start,
        )

    queued = traces("operation_queued", cutoff).with_only_columns(Raw.user_id, Raw.trace_id).distinct().subquery()
    terminals = traces("operation_terminal", raw_cutoff).subquery()
    match = and_(queued.c.user_id == terminals.c.user_id, queued.c.trace_id == terminals.c.trace_id)
    counts = Counter()
    for payload, in db.execute(select(terminals.c.payload).select_from(queued.outerjoin(terminals, match))).yield_per(250):
        counts[payload["outcome"] if payload else "pending_or_unresolved"] += 1
    all_queued = traces("operation_queued", raw_cutoff).with_only_columns(Raw.user_id, Raw.trace_id).distinct().subquery()
    unmatched = db.scalar(select(func.count()).select_from(terminals.outerjoin(
        all_queued, and_(all_queued.c.user_id == terminals.c.user_id, all_queued.c.trace_id == terminals.c.trace_id),
    )).where(all_queued.c.trace_id.is_(None)))
    return {"observed_enqueued_traces": sum(counts.values()),
            **{outcome: counts[outcome] for outcome in ("acknowledged", "superseded", "cancelled", "pending_or_unresolved")},
            "terminals_without_retained_enqueue": unmatched}


def _queue_gauges(db, environment, raw_cutoff, now):
    observed_ms = cast(Raw.payload["occurred_at_ms"].as_string(), BigInteger)
    latest = select(
        Raw.payload, Raw.received_at,
        func.row_number().over(partition_by=Raw.user_id, order_by=(Raw.received_at.desc(), observed_ms.desc(), Raw.event_id.desc())).label("position"),
        func.count().over(partition_by=(Raw.user_id, Raw.received_at, observed_ms)).label("tied_samples"),
    ).where(Raw.environment == environment, Raw.event_type == "queue_snapshot", Raw.received_at >= raw_cutoff).subquery()
    counts = Counter()
    for payload, received_at, tied_samples in db.execute(select(latest.c.payload, latest.c.received_at, latest.c.tied_samples).where(latest.c.position == 1)).yield_per(250):
        observed_age_ms = int(now.timestamp() * 1000) - payload["occurred_at_ms"]
        counts["sampled_accounts"] += 1
        if payload.get("clock_invalid") or observed_age_ms < 0 or tied_samples > 1:
            counts["unknown_sampled_accounts"] += 1
            continue
        counts["known_sampled_accounts"] += 1
        fresh = observed_age_ms <= 300000 and now - utc(received_at) <= timedelta(minutes=5)
        counts["fresh_sampled_accounts" if fresh else "stale_sampled_accounts"] += 1
        counts["latest_observed_depth"] += payload["queue_depth"]
        if fresh:
            counts["fresh_observed_depth"] += payload["queue_depth"]
        if payload.get("unknown_age_count") is None:
            counts["samples_with_unknown_age_coverage"] += 1
        else:
            counts["latest_unknown_age_count"] += payload["unknown_age_count"]
    return {
        "semantics": "Latest retained sample per account, not additive snapshots or current global queue depth",
        **{key: counts[key] for key in ("sampled_accounts", "known_sampled_accounts", "unknown_sampled_accounts", "fresh_sampled_accounts", "stale_sampled_accounts", "samples_with_unknown_age_coverage")},
        "latest_observed_depth": counts["latest_observed_depth"] if counts["known_sampled_accounts"] else None,
        "fresh_observed_depth": counts["fresh_observed_depth"] if counts["fresh_sampled_accounts"] else None,
        "latest_unknown_age_count": counts["latest_unknown_age_count"] if counts["known_sampled_accounts"] > counts["samples_with_unknown_age_coverage"] else None,
    }


def diagnostic_report(db: Session, *, days=7, now=None):
    if type(days) is not int or not 1 <= days <= AGGREGATE_DAYS:
        raise ValueError("days must be between 1 and 90")
    now = utc(now or datetime.now(timezone.utc))
    environment = flag_environment()
    first_date = now.date() - timedelta(days=days - 1)
    raw_cutoff = now - timedelta(days=RAW_DAYS)
    cutoff = max(raw_cutoff, datetime.combine(first_date, datetime.min.time(), tzinfo=timezone.utc))
    rows = db.scalars(select(Aggregate).where(Aggregate.environment == environment, Aggregate.bucket_date >= first_date)).all()
    events = [row for row in rows if row.metric == "events"]

    def count(**filters):
        return sum(row.sample_count for row in events if all(getattr(row, key) == value for key, value in filters.items()))

    storage_success = count(event_type="local_save", outcome="success")
    storage_failure = count(event_type="local_save", outcome="failure", error_category="storage")
    storage_known = storage_success + storage_failure
    request_results = count(event_type="sync_result")
    request_failures = count(event_type="sync_result", outcome="failure")
    categories = Counter()
    dimensions = defaultdict(int)
    for row in events:
        if row.event_type == "sync_result" and row.outcome == "failure":
            categories[row.error_category or "unknown"] += row.sample_count
        dimensions[(row.platform, row.app_version, row.event_type, row.operation_type, row.outcome, row.error_category,
                    row.acknowledgment_kind, row.recovered_after_restart, row.finish_stage)] += row.sample_count
    return {
        "observed_only": True, "environment": environment,
        "window": {"aggregate_first_date": first_date.isoformat(), "raw_from": cutoff.isoformat(), "received_before": now.isoformat(),
                   "raw_retention_days": RAW_DAYS, "anonymous_aggregate_days": AGGREGATE_DAYS},
        "coverage": {"all_workouts_denominator": "unknown: only enabled, delivered diagnostics are observed",
                     "dropped_diagnostics": sum(row.total for row in rows if row.metric == "dropped_count"),
                     "clock_invalid_events": sum(row.sample_count for row in rows if row.metric == "clock_invalid"),
                     "acknowledged_with_unknown_age": sum(row.sample_count for row in rows if row.metric == "unknown_queue_age"),
                     "limitations": "Missing reports, pending work, retention boundaries and intentional supersession are not evidence of lost workout data; no reliability SLO is inferred."},
        "local_writes": {"successful_attempts": storage_success, "storage_failed_attempts": storage_failure,
                         "storage_outcome_denominator": storage_known,
                         "storage_failure_share": storage_failure / storage_known if storage_known else None,
                         "validation_rejections": count(event_type="local_save", error_category="validation"),
                         "admission_conflicts": count(event_type="local_save", error_category="admission_conflict"),
                         "unknown_failure_category": count(event_type="local_save", outcome="failure") - storage_failure
                         - count(event_type="local_save", error_category="validation") - count(event_type="local_save", error_category="admission_conflict"),
                         "saved_sets": sum(row.total for row in rows if row.metric == "saved_count"),
                         "successful_duration": _distribution(rows, "duration_ms", event_type="local_save", outcome="success"),
                         "storage_failure_duration": _distribution(rows, "duration_ms", event_type="local_save", error_category="storage")},
        "write_requests": {"attempts": count(event_type="sync_attempt"), "results": request_results, "failures": request_failures,
                           "observed_result_denominator": request_results,
                           "failure_share": request_failures / request_results if request_results else None,
                           "attempt_result_count_gap": count(event_type="sync_attempt") - request_results,
                           "failure_categories": dict(categories), "retry_schedules": count(event_type="retry_scheduled"),
                           "duration": _distribution(rows, "duration_ms", event_type="sync_result"),
                           "retry_delay": _distribution(rows, "retry_delay_ms")},
        "terminal_outcomes": {outcome: count(event_type="operation_terminal", outcome=outcome) for outcome in ("acknowledged", "superseded", "cancelled")},
        "acknowledgments": {
            "direct": count(event_type="operation_terminal", acknowledgment_kind="direct"),
            "reconciled": count(event_type="operation_terminal", acknowledgment_kind="reconciled"),
            "recovered_after_restart": count(event_type="operation_terminal", outcome="acknowledged", recovered_after_restart="true"),
            "not_recovered_after_restart": count(event_type="operation_terminal", outcome="acknowledged", recovered_after_restart="false"),
            "restart_context_unknown": count(event_type="operation_terminal", outcome="acknowledged", recovered_after_restart="unknown"),
        },
        "queue_to_ack_age": _distribution(rows, "queue_age_ms"),
        "trace_cohort": _cohorts(db, environment, cutoff, raw_cutoff),
        "queue_gauges": _queue_gauges(db, environment, raw_cutoff, now),
        "finish": {stage: {"observed_results": count(event_type="finish_result", finish_stage=stage),
                           "successes": count(event_type="finish_result", finish_stage=stage, outcome="success"),
                           "failures": count(event_type="finish_result", finish_stage=stage, outcome="failure"),
                           "duration": _distribution(rows, "duration_ms", event_type="finish_result", finish_stage=stage)}
                   for stage in ("server", "local_cleanup")},
        "event_dimensions": [{**dict(zip(("platform", "app_version", "event_type", "operation_type", "outcome", "error_category", "acknowledgment_kind", "recovered_after_restart", "finish_stage"), key)), "observed_events": value}
                             for key, value in sorted(dimensions.items())],
    }
