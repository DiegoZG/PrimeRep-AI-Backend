# RevenueCat subscriptions

The billing foundation uses the existing environment-scoped rollout flags. All remain off until an operator changes them. It does not accept client entitlement claims, alter subscription prices, or activate enforcement in a migration. New-training entrypoints enforce server admission when enabled; local set logging and existing-session writes do not call RevenueCat.

## Configuration

Set these independently for each backend environment; never put server keys in the mobile app:

| Variable | Purpose |
| --- | --- |
| `REVENUECAT_SECRET_API_KEY` | Secret API v1 key for the RevenueCat project matching this environment. |
| `REVENUECAT_PRODUCT_IDS` | Comma-separated monthly/annual product IDs attached to `premium`. |
| `REVENUECAT_APP_IDS` | Comma-separated RevenueCat app IDs allowed at this webhook destination. |
| `REVENUECAT_WEBHOOK_AUTHORIZATION` | Exact Authorization value configured on the webhook integration. |
| `REVENUECAT_WEBHOOK_SIGNING_SECRET` | Optional dashboard HMAC signing secret. When set, every notification must have a valid raw-body signature within five minutes. |
| `REVENUECAT_PRODUCTION_TEST_USER_IDS` | Optional explicit UUID allowlist for Apple sandbox/App Review receipts on production. A feature flag Allow rule does not grant this exemption. |

Use separate RevenueCat projects/configuration and webhook destinations for preview and production. Public SDK keys, app IDs, product IDs, and server secret keys must belong to the same project. The REST secret scopes customer reads to that project. Missing provider configuration leaves training unchanged with enforcement off; enforced accounts without a transition/verified access receive a retryable verification error, not a purchase demand.

For local preview testing, PrimeRepAI's Test Store app is `app116807ef76` in project `proja9fc1792`. Set `REVENUECAT_PRODUCT_IDS=primerep_premium_monthly_test,primerep_premium_annual_test` and `REVENUECAT_APP_IDS=app116807ef76`. The approved API v1 key named `PrimeRep Preview Backend` is stored only in the ignored local `.env`; never commit or copy it to the mobile app. The `premium` entitlement and `default` offering include both products. Apple products and a production RevenueCat project are still pending.

## API and worker

- `GET /v1/users/me/subscription` reads verified state and the current effective flags. It queues due refresh work without blocking the response on RevenueCat.
- `POST /v1/users/me/subscription/reconcile` verifies the authenticated UUID synchronously, limited to six requests per minute. It returns the same typed state; request bodies cannot grant access to any account.
- `POST /v1/billing/revenuecat/webhook` authenticates and durably records an event before acknowledgement. Duplicate event IDs are ignored. Transfer events enqueue known source and destination accounts. No event creates an account.

Run `python -m scripts.run_billing_worker --limit 100` every minute. Each invocation schedules a bounded due batch, processes queued refresh/erasure work, and prunes event metadata after thirty days and completed job identities after seven days. Failures back off with jitter; after twelve attempts they remain failed for operator review. A new distinct provider event reopens an exhausted reconciliation job; duplicate events and automatic retry paths do not reset its backoff. After fixing provider configuration, use `--retry-failed` to requeue a bounded failed batch. Monitor job age, failed count, and purchase-to-access latency. The worker must use the same `APP_ENV` and secrets as that environment’s API.

Normalize every configured Premium product transaction so a sandbox annual receipt cannot hide a valid production monthly receipt. The production RevenueCat project must also use Sandbox Testing Access → Allowed App User IDs only, matching the server review/test allowlist; this prevents same-product sandbox receipts from changing ordinary production entitlement access.

Snapshot writes lock the user before fetching RevenueCat so concurrent reconciliation and account deletion cannot apply stale state. The shared flag lock is acquired first to keep lock ordering consistent with the admin service. Requests apply only snapshots at least as recent as stored provider state. A cancellation keeps valid access; grace ends at the verified grace date. Temporary provider failure retains verified access only until its existing deadline. Confirmed absence/expiry is distinct from unknown renewal status.

The compatibility `subscription_tier` mirrors verified billing, but `has_premium` and paid admission never trust an arbitrary tier string. Transition grants use server account creation time, the permanently locked launch date, and its shared thirty-day deadline. No per-user flag change restarts that deadline.

Account deletion calls `enqueue_customer_erasure(db, user_id)` inside its deletion transaction. Jobs intentionally have no user foreign key so erasure survives deletion. Webhooks ignore unknown UUIDs. A fresh provider event during the retained erasure-tombstone window requeues provider erasure for that deleted UUID, without recreating an account or access; duplicate events do not reopen work. Deletion does not cancel Apple billing.

## Training and data preservation

With effective enforcement enabled, verified Premium or an unexpired fixed transition grant is required for new sessions, generated/forced weeks, Skip Day, planning changes, pre-start additions, program/custom-exercise authoring and activation, AI questions, and new Coach work. Q&A retains its existing daily and request quotas; access denial happens before either quota is consumed. Coach still requires user opt-in; with enforcement off its previous Premium-tier requirement remains in place.

Owned same-account session retries recover admission before checking current payment. All admitted-session logging, replay, finish, corrections, notes, arrangement, and replacement stay available after expiry. Stored weeks (including stale, historical, future, and program weeks) are returned unchanged when access is denied or unknown. Missing weeks and forced regeneration require access. No provider call is needed to read a stored week, preserve preferences, or cancel a program.

Training preferences and onboarding remain editable. Restricted preference changes are stored without deleting or refreshing existing weeks. Active-program cancellation returns **204 No Content** when generation is unavailable, changing activation metadata only; permitted cancellation retains the existing 200 week response. Scheduled cancellation already returns 204. Clients must accept an empty successful response and refresh an existing week without force.

Coach feed/history reads stay available, but new deterministic guidance, AI enrichment, and notification enqueue/send check current access per account. In-flight AI results are discarded after access expires. Already-enriched guidance remains readable/exportable while consent remains enabled, including after enforcement rollback; turning consent off retains the existing AI-copy cleanup. Background scans are bounded and include eligible transition accounts rather than filtering only the compatibility tier.

Guarded schedule mutations acquire the shared environment configuration lock before account/schedule rows. Coach worker claims acquire it before preference rows. This matches administrator configuration→account ordering and avoids lock cycles during rollout edits. Admission guards do not enqueue billing jobs while holding workout locks.

Personal exports include only that account's normalized subscription status, product, verification/access dates, renewal status, and fixed transition grants. They exclude provider response bodies, credentials, webhook metadata, and operational billing jobs. Deletion cascades local subscription/grant rows and leaves a bounded provider-erasure job; later webhooks cannot recreate deleted UUIDs or entitlement access. Deleting PrimeRep does not cancel Apple subscriptions. Update final public privacy/legal wording and Apple disclosures before sales.

## Migration collision repair

Merged main contained two different migrations with revision `a2b3c4d5e6f7`: session arrangements and feature flags. Alembic could not distinguish schemas already stamped with that ID. This branch keeps `a2b3c4d5e6f7` for arrangements, assigns flags unique revision `c4d5e6f7g8h9`, and puts existing targeting `b3c4d5e6f7g8` after it. New repair revision `d5e6f7g8h9i0` ensures both legacy schemas are present before billing revision `e6f7g8h9i0j1`.

The schema checks preserve existing flag settings/audit history and arrangement data. They support fresh upgrades and old stamps with flags only, arrangements only, or both. Run ordinary `alembic upgrade head`; do not manually stamp forward, drop tables, downgrade a deployed database, or reset rollout configuration as a repair. The catch-up revision’s downgrade intentionally does not remove legacy schema whose original ownership cannot be reconstructed.

## Onboarding enrollment

The additive `f7g8h9i0j1k2` migration adds the default-off `subscriptions_onboarding_enabled` master, audience and user override, plus nullable enrollment/completion timestamps on environment-scoped subscription state. No existing user or subscription row is enrolled. Effective onboarding requires effective purchases and enforcement; global/audience/override changes retain versioning and audit history.

Fresh signup enrolls an account in the same transaction only when all three effective flags are on. The subscription snapshot and `/users/me` bootstrap response add `onboarding_subscription_required`, gated by completed onboarding and current effective flags. Provider uncertainty leaves enrolled accounts pending. Previously verified valid Premium/trial/grace or an unexpired transition grant permanently completes enrollment in the caller transaction; expiry never re-enrolls an account. Disabling a prerequisite releases the effective requirement without deleting its enrollment record. There is no client completion endpoint.

Deploy this migration before its paired frontend. Keep the new flag off until preview validation. Account export includes enrollment and completion dates; account deletion cascades the existing subscription-state row. Selected targeting does not retroactively enroll accounts created outside the enabled signup cohort.

## Verification

`pytest tests/test_subscriptions.py tests/test_subscription_guards.py tests/test_feature_flags.py tests/test_subscription_migrations.py` covers lifecycle, flags-off baseline, fixed transition boundaries, unknown verification, forged claims, webhook auth/HMAC/dedup, transfers, retries, deletion, and unique migration graph. Set `SUBSCRIPTION_MIGRATION_TEST_URL` to a local PostgreSQL database for the migration snapshot cases; the tests create/drop randomly named disposable databases without modifying existing application tables. Provider calls are mocked in these tests.

Known baseline test issue: `tests/test_coach_service.py::test_duration_change_uses_single_day_coach_not_week_batch` fails unchanged on foundation commit `2b913c1`: it edits the first day of the current week even when that date is past (409), and its legacy single-day Coach expectation is no longer produced by the existing planning regeneration path. This feature leaves that test's scenario/assertions unchanged; do not report the affected suite as entirely green. The separate historical-cancellation clock and concurrent Coach invalidation fixtures now establish their intended ordering explicitly without changing their assertions. A second unchanged baseline issue, `tests/test_workouts_next.py::test_workout_ppl_split`, assumes `/next` belongs to the current week; on a Sunday with no remaining scheduled days the API correctly selects the following week, so that ID assertion fails. Both failures were reproduced against the exact unchanged foundation commit in disposable databases.

Before sales, configure real Apple products/prices/trials, provider keys and webhook integration, and verify native Apple sandbox lifecycle/restore on a physical device. No real provider/store configuration is implied by passing fixture tests.

References: [RevenueCat webhooks](https://www.revenuecat.com/docs/integrations/webhooks), [REST Customer Info model](https://www.revenuecat.com/docs/api-v1/customer-info-model), [restore behavior](https://www.revenuecat.com/docs/projects/restore-behavior).
