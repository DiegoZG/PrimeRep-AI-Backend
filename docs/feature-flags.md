# Subscription feature flags

This foundation stores subscription rollout configuration and its audit trail in
the backend database. It does not yet connect purchases, paywalls, entitlement
checks, or workout restrictions. Changing these flags alone does not launch Part 11.

## Bootstrap and administrator access

Apply the additive migration with the normal deployment migration process:
`alembic upgrade head`. No configuration row is seeded; authenticated reads return
version `0`, both flags `false`, no dates, and no audit records until the first change.

Use the existing account UUID from the target environment's `users` table. The
requested administrator is `diego.zegarra78@gmail.com`; verify the exact account
in each environment with a read-only lookup before configuring its UUID:

```sql
SELECT id FROM users WHERE email = 'diego.zegarra78@gmail.com';
```

Set backend `FEATURE_FLAG_ADMIN_USER_IDS=<verified-user-uuid>` in the hosting
provider's secrets/configuration (or local `.env`) and restart backend processes.
Multiple UUIDs may be comma-separated. Empty denies everyone. Invalid UUIDs fail
startup; emails are not accepted as authorization identities. Users cannot assign
themselves administrator access. No user role or password changes are required.

The allowlist is deployment configuration; update it separately for preview and
production and restart all workers after changing it. Keep database and deployment
access restricted to operators. Never put the allowlist in the mobile app.

## Environment isolation and live changes

Every read/write uses the server's `APP_ENV`; the client cannot select an
environment. Values are normalized to lowercase, with `prod` and `production`
sharing `production`. Local, test, preview, staging, and production have separate
rows even if they share a database. Separate databases remain recommended for
preview and production.

Flags are read from the database for every request without a process cache, so
saved changes reach every backend worker without restarting or shipping an app.
The current app refreshes when Settings receives focus, through explicit admin
refresh, and after admin saves. An open or offline screen may keep its last
displayed snapshot until the next successful refresh. Future billing consumers
must fetch current flags and refresh when the app returns to the foreground;
backend training gates must read current server state at the operation.

## API

All endpoints require the existing bearer authentication:

- `GET /v1/feature-flags`: current environment, version, both subscription flags,
  launch/transition dates, launch lock, update time, and `can_manage_flags`.
- `GET /v1/admin/feature-flags`: same snapshot plus the latest twenty audit entries,
  newest version first. Allowlisted UUIDs only; other users receive `403`.
- `PATCH /v1/admin/feature-flags`: allowlisted UUIDs only; submit the last observed
  `expected_version`, any changed flag fields, and optionally a launch date.

Example PATCH:

```json
{
  "expected_version": 0,
  "flags": {
    "subscriptions_purchases_enabled": true
  },
  "subscriptions_launch_at": "2026-11-01T00:00:00Z"
}
```

Unknown keys, coercible booleans, invalid versions, and launch dates without a
timezone receive `422`. Timestamps with explicit offsets are converted to UTC.
An omitted launch date leaves it unchanged; `null` clears it before launch lock.
Stale saves (including simultaneous first writes) receive `409`; reload, review
the latest configuration, and submit that version. All errors expose readable
`detail` strings. No-op saves preserve the version and create no audit entry.

Successful changes atomically increment the version and write the actor UUID,
time, and complete before/after flag/date/lock values. Public reads never include
audit data. Deleting an actor nulls the actor UUID while retaining audit records.

## Launch rules and rollback

Enforcement requires purchases to be enabled and a configured launch timestamp
at or before current UTC time. The first enforcement enable permanently freezes
that timestamp. It cannot be changed or cleared after turning enforcement off.
The transition deadline is always launch plus thirty days; toggling flags does
not reset it. A displayed deadline before first enforcement is provisional.

Purchases may be enabled before enforcement to test billing. Purchases cannot be
disabled while enforcement remains enabled; disable enforcement first, or submit
both flags `false` in one atomic change. This is the intended incident rollback
configuration once Part 11 gates are connected. Preserve receipt verification,
webhooks, restore, and subscriber management independently of purchase visibility.

Reverting application code or changing `APP_ENV` is not a flag rollback. Keep the
tables and audit history when rolling back app releases; dropping the migration
destroys configuration/history and must not be used as an incident switch.
