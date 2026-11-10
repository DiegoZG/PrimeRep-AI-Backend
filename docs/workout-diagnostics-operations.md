# Workout reliability observations

Collection is disabled by default through the independent `workout_reliability_monitoring_enabled` flag. Subscription flags do not control collection. Enable it only for selected preview accounts initially; keep production off until capture, retention scheduling and operator ownership are verified. The API writes diagnostics in its own request transaction, never in a workout mutation transaction. It uses no external telemetry provider.

The [v1 contract](workout-diagnostics-contract.md) defines strict allowlisted fields, event meanings and denominators. Invalid bodies receive a fixed validation message without rejected input. The API enforces the actual streamed 32 KiB body limit, 20-event batch limit, authenticated ownership and server environment. Private raw events are deduplicated by owner/environment/event ID and terminal outcomes additionally by owner/environment/trace ID using database constraints. Concurrent accepted inserts and anonymous aggregate increments share one diagnostics transaction. Diagnostic receipts do not confirm workout persistence.

## Operator commands

Run these commands with the deployed environment's normal `DATABASE_URL` and `APP_ENV`; never pass an account selector, credential or environment override in diagnostic JSON. As with the billing worker, commands load the application's environment. Avoid printing environment files.

```bash
python -m scripts.workout_diagnostics report --days 7
python -m scripts.workout_diagnostics report --days 90
python -m scripts.workout_diagnostics prune
```

`report` is read-only (PostgreSQL also enforces a read-only transaction). It prints JSON with observed local write/storage failures, saved-set counts, write-request attempts/results, retry categories/delays, acknowledgment age histograms, direct/reconciled and restart-recovery dimensions, separate server/local-cleanup finish results, and collection drops. Counts group by fixed event/operation/error/outcome categories, platform and release. Reports and maintenance output contain no user, event or trace identifiers. There is no public diagnostics read API.

Distributions expose known sample counts, means and fixed noncumulative histogram bounds, not exact percentiles. An empty distribution has an unknown (`null`) mean. Acknowledgment age excludes invalid clocks and unknown legacy ages; the report shows their coverage gap. Request-attempt/result count differences can arise at collection or reporting boundaries and are not a count of lost writes. Local storage-failure share uses only observed successful or storage-failed durable write attempts; validation/admission failures stay separate. Check event dimensions when an unknown failure category appears.

Trace cohorts use the retained 14-day raw window, even when aggregate trends cover 90 days. They distinguish acknowledged, intentionally superseded/cancelled, pending/unresolved, and terminals without a retained enqueue. No terminal observed means unresolved within the window, not failed. Queue snapshots are nonadditive: the report uses the latest retained sample per account, shows stale samples separately, and calls a sample fresh only with a valid observed time and receipt within five minutes. Within a received batch, valid observation times break receipt-time ties; indistinguishable observation times or invalid clocks make that account's gauge unknown and exclude its depth from totals. Accounts without samples are unobserved. A sampled queue total is not the current global queue depth or a loss metric.

## Required deployment maintenance

This repository documents platform cron jobs for Coach and billing; it does not provision a production scheduler. **Deployment must configure and monitor an hourly job** using the same application environment:

```cron
0 * * * * cd /path/to/primerep-backend && /path/to/venv/bin/python -m scripts.workout_diagnostics prune
```

The command deletes only the current server environment's raw rows older than 14 days and anonymous day buckets outside the most recent 90 calendar dates. Other environments are untouched. Pruning is idempotent and prints aggregate deletion counts only. Raw exports and reports already exclude expired data; physical removal requires this scheduled job, with up to one scheduling interval of deletion lag. Anonymous buckets hold counts, bounded duration histograms/sums and fixed categories; they contain no identity or correlation fields. Raw rows cascade on account deletion and owned retained rows appear in the existing personal-data export. Deleting an account does not require rewriting identity-free aggregate counts.

Before production collection, Diego/the deployment owner must verify a successful scheduled run and assign ownership of failed/missed runs. Keep operator readouts internal, begin with a weekly summary, and establish thresholds from measured baseline volume. No paging thresholds or reliability SLO are asserted by this slice. Delivery drops, disabled collection, invalid clocks, missing reports and retention boundaries limit coverage; these observations cannot establish 99.9% reliability or a data-loss percentage.
