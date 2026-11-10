# Workout diagnostics v1 contract

Implementation reference: `app/schemas/workout_diagnostics.py`, `app/api/v1/workout_diagnostics.py` and `app/core/workout_diagnostics_service.py`.

## Rollout and transport

- Independent effective flag: `workout_reliability_monitoring_enabled`, default false. It uses existing everyone/selected audience, per-user allow/deny/inherit, versions and audit history. Subscription flags are not prerequisites.
- Endpoint: authenticated `POST /v1/diagnostics/workout-sync`, limited to 30 batches per minute per authenticated account using the existing limiter storage. Rate rejection does not log the account's limit key. HTTP 429 returns `Retry-After: 60`; HTTP 503 means diagnostic storage/admission is temporarily unavailable. Both remain background diagnostic delivery conditions.
- Maximum request body: 32 KiB; JSON batch contains 1–20 events.
- Account identity comes from the bearer token. Environment comes from backend `APP_ENV`/existing `flag_environment()`. User/environment selectors are forbidden in JSON and query parameters.
- Extra keys are forbidden at both batch and event levels. Validation errors must be sanitized and never echo rejected input.
- Successful ingestion response: `{"enabled":true,"accepted":1,"duplicates":0,"discarded":0}`.
- Disabled effective flag response: HTTP 200, `{"enabled":false,"accepted":0,"duplicates":0,"discarded":N}`. No events are stored. The client can retire that delivered batch and suspend delivery until a later enabled flag snapshot.
- Diagnostic delivery must not refresh tokens in a loop, log out an account, open a paywall or affect workout persistence/replay.

```json
{
  "schema_version": 1,
  "platform": "ios",
  "app_version": "1.0.0",
  "events": [
    {
      "event_id": "154d6b98-665b-4e9f-8d50-f6abef774f38",
      "event_type": "operation_terminal",
      "occurred_at_ms": 1794081600000,
      "trace_id": "c98d5494-4b8f-4b92-bf1d-1903791239fd",
      "operation_type": "set",
      "outcome": "acknowledged",
      "acknowledgment_kind": "reconciled",
      "queue_age_ms": 3200,
      "recovered_after_restart": true
    }
  ]
}
```

## Required fields

Batch: `schema_version` is strictly integer `1` (not boolean/string); `platform` is `ios|android|web`; `app_version` is `unknown` or a 1–64-character release string beginning with a digit and containing only letters/digits/dot/plus/hyphen; `events` has 1–20 entries.

Each event requires UUID `event_id`, `event_type`, and strict integer `occurred_at_ms` in `[0, 8640000000000000]`. Event IDs stay unchanged across delivery retries. `trace_id` is an independently generated diagnostic UUID, not a workout/session/set ID.

## Fixed enums

- `event_type`: `local_save`, `operation_queued`, `sync_attempt`, `sync_result`, `retry_scheduled`, `operation_terminal`, `queue_snapshot`, `finish_result`, `diagnostics_dropped`.
- `operation_type`: `create`, `set`, `update_set`, `delete_set`, `restore_set`, `update_workout_note`, `update_exercise_feedback`, `update_exercise_note`, `order`, `replace`, `complete`, `abandon`.
- Result `outcome`: `success|failure`. Terminal `outcome`: `acknowledged|superseded|cancelled`.
- `error_category`: `network`, `timeout`, `auth`, `rate_limited`, `server`, `validation`, `admission_conflict`, `storage`, `unknown`.
- `acknowledgment_kind`: `direct|reconciled`, only for acknowledged terminal operations.
- `finish_stage`: `server|local_cleanup`, only for `finish_result`.

Request failures are never terminal; they can subsequently recover. A missing report is not a data-loss event.

## Optional fields and bounds

All numeric fields below are strict integers (not booleans, numeric strings or fractional numbers). Missing fields mean unknown/not applicable, not zero.

| Field | Bound / meaning |
| --- | --- |
| `duration_ms` | 0–86400000, observed monotonic elapsed duration |
| `queue_age_ms` | 0–31536000000, elapsed wall-clock age from initial immutable enqueue; omitted for unknown legacy age or invalid clocks |
| `retry_delay_ms` | 0–86400000 |
| `queue_depth`, `unknown_age_count`, `saved_count`, `dropped_count`, `request_attempt` | 0–1000000 |
| `http_status` | 100–599 |
| `recovered_after_restart`, `player_active`, `clock_invalid` | strict booleans |

`trace_id`, `operation_type`, `outcome`, `error_category`, `acknowledgment_kind` and `finish_stage` use the enums/types above. No freeform exception or business payload fields exist.

## Event invariants

- `operation_queued`, `sync_attempt`, `sync_result`, `retry_scheduled` and `operation_terminal` require `trace_id` and `operation_type`.
- `local_save`, `sync_result` and `finish_result` require `success|failure` outcome.
- Successful `local_save` requires positive `saved_count`; success is emitted only after the existing durable device write.
- `operation_terminal` requires an acknowledged/superseded/cancelled outcome; acknowledged additionally requires `acknowledgment_kind`.
- Only result or terminal events accept `outcome`. Error categories require a failure or `retry_scheduled`.
- `queue_snapshot` requires `queue_depth`; `unknown_age_count` cannot exceed it.
- `diagnostics_dropped` requires `dropped_count`.
- `finish_result` requires `finish_stage`.
- `clock_invalid=true` cannot include `queue_age_ms`.

## Event-to-metric rules

These are observed diagnostics under the enabled flag, not a census of all workouts. Count deduplicated events; count logical terminal outcomes only once per authenticated owner/environment/`trace_id`. A diagnostic receipt confirms telemetry delivery, never a workout save.

| Event | Metric / denominator | Interpretation |
| --- | --- | --- |
| `local_save` | Local-write duration distribution over observed completed durable write attempts with known duration; storage-failure share over observed successful or storage-failed write attempts. Report successful `saved_count` separately. | Success means the existing device write completed durably and `saved_count > 0`. Validation rejection and admission conflict are separate categories, excluded from storage-failure numerators and denominators. A batch write is one attempt, not one attempt per saved set. |
| `operation_queued` | Observed enqueue count and trace cohort, grouped by fixed operation type. | Only new logical operations enter the cohort; delivery retries and restart do not create new traces. Pre-instrumentation operations may lack enqueue coverage. |
| `sync_attempt` | Actual mutating replay write-request attempt count. | Emit only when replay calls a mutating service (create/log/update/delete/restore/order/replace/complete/abandon/notes), never for canonical GETs, polling, guard checks, scheduled retries or local reconciliation. |
| `sync_result` | Write-request success/failure counts and failure share among observed write-request results; duration distribution among results with known duration. | Canonical GET-only reconciliation uses `acknowledgment_kind=reconciled` and creates no synthetic per-operation write request. Keep attempted writes without a result visible as unknown. Request failures remain nonterminal and can recover; they are not lost sets. |
| `retry_scheduled` | Retry scheduling count and delay distribution over observed scheduling events. | A retry is not an additional request or additional failure until an actual request/result occurs. |
| `operation_terminal` | Unique acknowledged/superseded/cancelled trace counts; queue-to-ack age distribution over unique acknowledged traces with known valid age; direct/reconciled and restart-recovery shares among acknowledged traces with those fields known. | Acknowledgment follows the existing durable canonical merge. Superseded and cancelled work are intentional terminal outcomes, separate from acknowledgment and data loss. Never divide acknowledgments by HTTP attempts. |
| `queue_snapshot` | Latest observed queue depth and unknown-age coverage for the sampled scope/time. | Snapshots are gauges, never additive counts or proof of loss. Reports select the latest received sample, breaking receipt-time ties with valid observation time; delayed samples stay stale and ambiguous/invalid-clock samples stay unknown. No snapshot-age distribution is provided; queue-to-ack distributions come from acknowledged terminal events. Do not zero-fill missing coverage. |
| `finish_result` | Success/failure counts and duration distributions separately for `server` and `local_cleanup`, using observed results for that same stage as the denominator. | Server completion and local cleanup are distinct stages; neither substitutes for the other or creates a second terminal operation. |
| `diagnostics_dropped` | Sum of deduplicated `dropped_count` by report bucket. | Signals collection gaps caused by bounded telemetry storage/delivery, not dropped workout operations. Report gaps alongside the other metrics. |

For captured enqueue cohorts, a trace without an observed terminal outcome is **pending/unresolved in the observation window**, not failed. Keep superseded/cancelled traces separate. Unmatched terminal events, missing enqueue coverage, invalid/legacy ages, stale snapshots and missing request results are **unknown coverage**, not zero latency or confirmed loss. Retention boundaries can leave unmatched traces; state the reporting window and known sample count beside each distribution. Do not derive a 99.9% reliability claim or a data-loss percentage from acknowledgments alone.

## Retention and privacy

Raw allowlisted diagnostic events and their private owner association expire **14 days after server receipt**. Identity-free aggregate buckets expire after **90 days** and contain only fixed low-cardinality dimensions and bounded counts/distributions; they contain no owner, event or trace IDs. Scheduled pruning must cover both windows. Exact-event and logical-terminal deduplication is bounded by the raw retention window; reports must disclose that boundary rather than claiming lifetime deduplication.

Owned raw diagnostics follow the existing explicit-field data export and account-deletion paths. The backend stores only allowlisted fields and the private authenticated owner association required for isolation/export/deletion. Never store training values, exercise identity, notes, photos, emails, tokens, arbitrary URLs, request/response bodies or raw exception messages in diagnostics.

## Time, ordering and deduplication

Server receipt time controls retention and report buckets. Client event time is never used as the server's authoritative clock. During ingestion, event times more than seven days behind or five minutes ahead of receipt are classified as clock-invalid and their queue age is omitted; they are not silently assigned age zero or used in queue-age distributions. Valid operation ages above the one-year bound must also be reported as unknown by the client. Live durations use a monotonic clock.

Deduplicate exact events by authenticated owner, server environment and `event_id`. Additionally, accept only one logical terminal outcome per owner/environment/`trace_id`, even when the client generates a different event ID after restart. Idempotent retries return duplicate counts instead of inflating aggregates. Never use trace/event/owner IDs as metric labels. Deduplication is bounded by the 14-day raw retention window; diagnostics remain observational rather than proof of zero data loss.
