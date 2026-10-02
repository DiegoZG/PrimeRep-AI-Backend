# Part 10 — Health connection and Coach foundation (backend)

## What changed

- Added authenticated, camelCase Health APIs for connection state, source consent, preferences, revision-fenced daily-summary uploads, and disconnect. The server stores daily step totals and asleep minutes by user, source, local date, and IANA timezone—not raw samples.
- Added idempotent 30-day-window upload behavior, a rolling 90-day retention policy and purge command, primary-source selection, stale-upload rejection, and deletion of a disconnected source's summaries and derived Coach context.
- Added an explicit workout-export consent epoch and source-revision fencing. A follow-on migration resets unverified legacy export toggles instead of fabricating consent; new workout export requires an explicit opt-in after this change. Existing connections can separately withdraw read or export consent even while collection is disabled.
- Added at most one rules-based Health Coach item per day when the user opts in and sufficient primary-source data exists. It is not AI-assisted, does not alter the workout plan, and does not create a medical or readiness score.
- Included retained daily summaries in account export (JSON and readable CSV) and removed health connections/summaries on account deletion. Health values are excluded from external AI requests and privacy-unsafe logging.

## Verification

- **491 backend tests passed** against a disposable database, including Health API/consent, stale-revision, Coach, export, retention, and deletion coverage. `git diff --check` passed.
- The physical-iPhone HealthKit bridge diagnostic is covered in the [frontend Part 10 PR](https://github.com/DiegoZG/PrimeRep-AI/pull/43); it does not prove this backend's production consent/sync flow.

## Rollout and open gates

Merge and deploy this backward-compatible backend PR before the frontend PR. Keep `HEALTH_COLLECTION_ENABLED=false` until the revised privacy policy is approved and published with renewed user acceptance, Apple Health capability/disclosures and platform declarations are verified, and the retention purge command is scheduled in production. The server's connection record is PrimeRep consent state, **not** proof of OS authorization.

Full on-device connection/export testing, including offline completion, retry, revocation, disconnect, and duplicate prevention, remains outstanding. Android Health Connect implementation/testing is deferred by the iOS-first decision; do not enable Android collection from this backend foundation alone. Part 9 production infrastructure/restore gates remain separate.
