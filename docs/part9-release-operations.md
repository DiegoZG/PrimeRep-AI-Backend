# Part 9 release operations and decision record

Status: engineering in progress; production release blocked by the external decisions below. This document is a runbook, not evidence that a production environment exists.

## Environment decisions and owners

| Decision | Current choice / required evidence | Owner |
| --- | --- | --- |
| API host and region | Pending Diego's provider, region, public HTTPS API hostname, monthly budget, and trusted proxy IPs | Diego |
| PostgreSQL | Managed PostgreSQL with automated encrypted backups and point-in-time recovery; provider, retention, access owner, and cost pending | Diego |
| Redis | Shared managed Redis for API rate limits; provider and cost pending | Diego |
| Preview | Separate database, Redis, Resend key, and API/reset domains; must not use production data | Diego |
| Resend | Verify PrimeRep-owned sending domain, SPF/DKIM/DMARC and bounce handling; rotate the key previously exposed in development | Diego |
| App/reset URL | HTTPS reset page reachable from other devices and networks; hostname pending | Diego |
| Media | Current approved media can use the existing configured CDN; production domain and owner pending | Diego |
| Monitoring | Sentry project/account and alert recipient pending; no client DSN or crash upload is enabled yet | Diego |
| Legal | Terms and Privacy remain `pending_review`; external approval and published version evidence pending | Diego/legal reviewer |
| Store accounts | Apple/Google accounts, signing access, and internal-test ownership pending | Diego |

Initial recovery targets are at most one hour of database data loss and four hours to restore service. Confirm that the selected provider's backup/PITR plan can meet these targets and record actual drill results before release.

The initial release targets native iOS and Android plus the browser reset page. A public workout web app is not an initial release claim. Browser token storage is memory-only; native SQLite offline recovery does not apply to web. EAS Update is disabled for this release unless separately configured and tested with distinct preview/production channels and compatible runtime versions.

## Deployment sequence

1. Create separate preview and production secret stores. Set `APP_ENV`, `DATABASE_URL`, distinct 32+ character `JWT_SECRET` and `JWT_REFRESH_SECRET`, Redis `RATE_LIMIT_STORAGE_URI`, HTTPS `CORS_ALLOWED_ORIGINS`, `RESEND_API_KEY`, verified `EMAIL_FROM`, stable `EMAIL_OUTBOX_ENCRYPTION_KEY`, HTTPS `PASSWORD_RESET_URL_BASE`, and `RELEASE_VERSION`. Do not put secrets in `EXPO_PUBLIC_` values, Git, or build logs.
2. Provision managed PostgreSQL and Redis. Confirm the migration role can install `pg_trgm` and apply migrations. Pin the deployed image by digest; the current unpinned Python requirements are a release reproducibility gap to close before production.
3. Run `alembic upgrade head` as a one-off release job before shifting API traffic. Run `python scripts/release_preflight.py`; every check must be true. Do not run migrations from each API replica.
4. Start API replicas from `Dockerfile` behind a trusted TLS proxy. Configure `FORWARDED_ALLOW_IPS` with only the proxy address or CIDR. Check `GET /health` for liveness and `GET /ready` for database/Redis readiness. Never expose `/ready` as a public diagnostics dashboard.
5. Schedule `python scripts/run_email_worker.py` every minute and `python scripts/run_coach_worker.py` every five minutes, with exactly one scheduler per environment. Worker output is aggregate counts only. Alert on repeated `failed` email deliveries, a growing pending queue, and oldest pending reset email approaching 20 minutes.
6. Set the preview EAS `EXPO_PUBLIC_API_URL` to the preview HTTPS API and production EAS value to production HTTPS API. Confirm the value in each built binary. Never put a bearer token or service key in this setting. Build signed internal-test binaries and test them against the matching environment.
7. Smoke signup, login, password reset on phone and desktop, workout logging through an offline cold start, program activation, Coach, profile, export, and deletion. Inspect request IDs and logs for privacy. Promote only after backup restore and legal approval gates are met.

## Backup and restore drill

The selected managed PostgreSQL provider must take encrypted automated backups with point-in-time recovery. Record schedule, retention, encryption key owner, restore permissions, and the latest verified recovery point. For a drill, restore into a *new isolated database* with no production traffic or email/push workers. Verify counts and sample references for users, sessions/sets, program activations, legal acceptance versions, and exports. Run application read checks against that isolated target. Record the actual recovery point and elapsed restore time. Do not point a running production app at the restored database without an incident decision.

For a failed release, stop traffic shift and restore the prior compatible API image. Additive migrations are preferred; do not downgrade a live database containing new user writes without a reviewed data-loss plan. A rollback of frontend code, backend code, catalog pointers, and database state are separate decisions. Keep the previous internal-test app build available.

## Incident response

Alert destination, on-call person, and dashboard links: **pending provider/monitoring choice**. For an incident, record UTC start time, release version, affected subsystem, request IDs, mitigation, and user impact; never paste tokens, emails, notes, questions, or raw export contents into the incident record.

- API unavailable: check `/health`, `/ready`, database and Redis health, migration revision, then reverse proxy routing.
- Password reset delayed: inspect aggregate outbox pending/failed counts and worker schedule; check Resend provider status and verified domain. Do not query or disclose raw reset links.
- Missing workout report: obtain the user's consent and use account-scoped support tooling to compare local pending operation IDs with canonical session and set IDs. Do not view another account's records or declare loss before checking the local queue.
- Sync backlog: check client pending/retry/failed states, API errors, session idempotency, and the oldest queued operation. Keep local data until the same identity reauthenticates and replay succeeds.
- Backup restore: use the isolated drill procedure above, then escalate before any production cutover.

## Release manifest template

Record for each candidate: UTC time, operator, backend commit and image digest, Alembic head, frontend commit, iOS/Android build numbers and EAS environment, API/reset/media hosts, Terms/Privacy versions and approval evidence, catalog release, backup recovery point, smoke-test result, monitoring dashboard, rollout percentage, and explicit go/no-go decision.
