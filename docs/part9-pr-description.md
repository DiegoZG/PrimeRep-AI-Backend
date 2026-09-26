# Part 9 backend — durable recovery email and release operations

## What changed

- Persist password-reset and password-change email in an encrypted transactional outbox before delivery. A one-shot worker retries provider failures, expires unusable reset links, and reports aggregate queue health without logging private content.
- Add production startup guards for secrets, Redis-backed rate limiting, HTTPS reset/CORS settings, and a verified sending address.
- Add lightweight liveness and dependency readiness checks, request IDs, safe route-template timing logs, a container recipe, and a release preflight command.
- Add the Part 9 implementation plan and operator runbook, including backend-first rollout, backup/restore, incident, and release-manifest procedures.
- Make two existing program-date tests deterministic across local/UTC day boundaries, and update stale `/workouts/next` assertions to reflect next-scheduled-day selection; program behavior is unchanged.
- Add regression coverage for logging missed sets after a session is completed, including idempotent replay, snapshot and account isolation, duplicate-slot rejection, and updated workout metrics. The existing set-logging API supports this flow; this follow-up adds no backend endpoint or migration.

## Verification

- Focused workout-editing, release-readiness, and password-reset suites: **38 passed**.
- Full backend suite: **470 passed**.
- Alembic upgrade → downgrade → upgrade passed on a disposable database on the local PostgreSQL server; the normal local database is at head `v7w8x9y0z1a2`.
- Release preflight passed against the local database with `APP_ENV=preview`; this is not a deployed preview environment.
- `git diff --check` passed.

## Rollout and open gates

Merge/deploy this backend PR before the frontend Part 9 PR. Apply migrations once before shifting API traffic; run the email worker every minute and the existing Coach worker on its documented schedule. Keep a stable `EMAIL_OUTBOX_ENCRYPTION_KEY` across releases.

This PR is an engineering foundation, **not production approval**. Provider/region, managed PostgreSQL and Redis, HTTPS domains, verified Resend sending domain and key rotation, monitoring owner, backup restore drill, legal approval, signed device builds, and store credentials remain pending. Python dependencies are not yet pinned; lock and verify the final deployment image before production. See `docs/part9-release-operations.md` for the gate list and runbook.
