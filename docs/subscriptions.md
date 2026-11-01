# RevenueCat subscriptions foundation

The billing foundation uses the existing environment-scoped rollout flags. Both remain off until an operator changes them. It does not accept client entitlement claims, alter subscription prices, or activate enforcement in a migration. New-training entrypoint integration is the next phase; local set logging and existing-session writes do not call RevenueCat.

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

## API and worker

- `GET /v1/users/me/subscription` reads verified state and the current effective flags. It queues due refresh work without blocking the response on RevenueCat.
- `POST /v1/users/me/subscription/reconcile` verifies the authenticated UUID synchronously, limited to six requests per minute. It returns the same typed state; request bodies cannot grant access to any account.
- `POST /v1/billing/revenuecat/webhook` authenticates and durably records an event before acknowledgement. Duplicate event IDs are ignored. Transfer events enqueue known source and destination accounts. No event creates an account.

Run `python -m scripts.run_billing_worker --limit 100` every minute. Each invocation schedules a bounded due batch, processes queued refresh/erasure work, and prunes event metadata after thirty days and completed job identities after seven days. Failures back off with jitter; after twelve attempts they remain failed for operator review. A new distinct provider event reopens an exhausted reconciliation job; duplicate events and automatic retry paths do not reset its backoff. After fixing provider configuration, use `--retry-failed` to requeue a bounded failed batch. Monitor job age, failed count, and purchase-to-access latency. The worker must use the same `APP_ENV` and secrets as that environment’s API.

Normalize every configured Premium product transaction so a sandbox annual receipt cannot hide a valid production monthly receipt. The production RevenueCat project must also use Sandbox Testing Access → Allowed App User IDs only, matching the server review/test allowlist; this prevents same-product sandbox receipts from changing ordinary production entitlement access.

Snapshot writes lock the user before fetching RevenueCat so concurrent reconciliation and account deletion cannot apply stale state. The shared flag lock is acquired first to keep lock ordering consistent with the admin service. Requests apply only snapshots at least as recent as stored provider state. A cancellation keeps valid access; grace ends at the verified grace date. Temporary provider failure retains verified access only until its existing deadline. Confirmed absence/expiry is distinct from unknown renewal status.

The compatibility `subscription_tier` mirrors verified billing, but `has_premium` and paid admission never trust an arbitrary tier string. Transition grants use server account creation time, the permanently locked launch date, and its shared thirty-day deadline. No per-user flag change restarts that deadline.

Account deletion integration must call `enqueue_customer_erasure(db, user_id)` inside its deletion transaction. Jobs intentionally have no user foreign key so erasure survives deletion. Webhooks ignore missing UUIDs; deletion does not cancel Apple billing.

## Migration collision repair

Merged main contained two different migrations with revision `a2b3c4d5e6f7`: session arrangements and feature flags. Alembic could not distinguish schemas already stamped with that ID. This branch keeps `a2b3c4d5e6f7` for arrangements, assigns flags unique revision `c4d5e6f7g8h9`, and puts existing targeting `b3c4d5e6f7g8` after it. New repair revision `d5e6f7g8h9i0` ensures both legacy schemas are present before billing revision `e6f7g8h9i0j1`.

The schema checks preserve existing flag settings/audit history and arrangement data. They support fresh upgrades and old stamps with flags only, arrangements only, or both. Run ordinary `alembic upgrade head`; do not manually stamp forward, drop tables, downgrade a deployed database, or reset rollout configuration as a repair. The catch-up revision’s downgrade intentionally does not remove legacy schema whose original ownership cannot be reconstructed.

## Verification

`pytest tests/test_subscriptions.py tests/test_feature_flags.py tests/test_subscription_migrations.py` covers lifecycle, flags-off baseline, fixed transition boundaries, unknown verification, forged claims, webhook auth/HMAC/dedup, transfers, retries, deletion, and unique migration graph. Set `SUBSCRIPTION_MIGRATION_TEST_URL` to a local PostgreSQL database for the migration snapshot cases; the tests create/drop randomly named disposable databases without modifying existing application tables. Provider calls are mocked in these tests.

Before sales, configure real Apple products/prices/trials, provider keys and webhook integration, and verify native Apple sandbox lifecycle/restore on a physical device. No real provider/store configuration is implied by passing fixture tests.

References: [RevenueCat webhooks](https://www.revenuecat.com/docs/integrations/webhooks), [REST Customer Info model](https://www.revenuecat.com/docs/api-v1/customer-info-model), [restore behavior](https://www.revenuecat.com/docs/projects/restore-behavior).
