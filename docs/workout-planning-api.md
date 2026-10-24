# Trainer-led workout planning

Deploy this API before the matching client. This change uses the existing
onboarding JSONB and weekly-plan JSONB; it requires no database migration.

## Defaults and compatibility

New accounts are stamped at creation with `workoutPlanningMode: trainer_led`
and `preferredWorkoutMinutes: 35`. Older onboarding writes preserve those
fields. Existing accounts with absent planning fields remain `time_budget`
with a remembered 35-minute default. Reading existing plans adds explicit
planning metadata and computes estimates on copies; it does not regenerate
stored exercises, identities or prescriptions.

A workout has `planningMode` (`trainer_led` or `time_budget`),
`timeBudgetMinutes` (null for Trainer-led), `estimatedMinutes`, and
`planningSource` (`default` or `override`). `durationMinutes` remains an integer
for legacy clients: the cap for a timed workout, the estimate for a generated
Trainer-led workout. Program duration remains authored; programs report
Trainer-led with no user cap and remain locked to their authored targets.
`planningEditable` is false for program or started/completed sessions. Clients
also apply their local-date past-workout guard; mutation authority is server-side.

## Saved preferences

`GET /v1/users/me/training-preferences` includes:

```json
{"workoutPlanningMode":"trainer_led","preferredWorkoutMinutes":35}
```

PATCH accepts either field, remembers minutes independently of mode, and
validates integer budgets from 15 to 120. The dedicated planning save uses
`?planUpdateScope=preserve` to retain current plans. Omission keeps the existing
refresh behavior for the separate general-preference save. Saving valid settings
and applying them are separate actions: a failed rebuild does not undo a saved
preference.

## Workout override

`PATCH /v1/workouts/week/planning` returns the existing weekly-plan response:

```json
{"workoutDayId":"opaque-id","planningMode":"time_budget","timeBudgetMinutes":90,
 "weekStart":"2026-10-19","effectiveDate":"2026-10-24"}
```

Trainer-led requests send `timeBudgetMinutes: null`. This changes only the
selected eligible workout and marks it `planningSource: override`.
`PATCH /v1/workouts/week/duration` remains a compatibility adapter that sets a
time-budget override. Past, started/completed and program workouts are protected.

## Explicit application

`POST /v1/workouts/week/apply-planning-preference` accepts paired `weekStart`
and `effectiveDate` local-calendar dates. It returns:

```json
{"weekPlan":{},"updatedWorkoutCount":3,"protectedWorkoutCount":2}
```

It rebuilds remaining unstarted generated workouts in the current and stored
future weeks using saved planning preferences, resetting their day overrides.
Past days, active/completed session IDs, authored program snapshots and the
scheduled-program boundary are protected. A fully protected week remains
unchanged, including its stored timestamp. The user schedule lock serializes
application with workout creation. All eligible weeks are staged before commit;
any failure leaves every plan unchanged.

## Planning and estimates

The full deterministic profile requests 2 main + 2 accessory exercises for
no-experience/beginner/unknown users, 3 + 3 for intermediate, and 3 + 4 for
advanced. Existing eligible-equipment filtering, split/muscle selection, coverage
balancing, main/accessory prescriptions and exercise-specific history remain in
use. A smaller eligible pool produces available work and coverage metadata.

Time budgets are upper limits. Generate the full profile, then remove accessory
tail items before optional main items, retaining up to two best available
selected exercises. Do not pad long budgets, invent exercises, lower rest, or
inflate volume to fill time. Deferred and unavailable muscle coverage stays
explicit. Trainer-led retains the full profile and has no time cap.

Estimate seconds from actual prescriptions: four seconds per average prescribed
rep, rest between sets (sets minus one), one minute between exercises, and five
minutes of preparation. Round up to five minutes. Estimates remain separate
from selected caps and actual session elapsed time. Coach action summaries use
exercise count for Trainer-led, `Up to N min` for timed workouts, and authored
program duration for programs.

If even the useful minimum cannot fit, return HTTP 409:

```json
{"detail":{"code":"workout_time_budget_too_short",
 "message":"Choose a larger budget or Trainer-led.","minimumUsefulMinutes":25}}
```

A protected generated workout returns `workout_planning_locked`; a program
returns `program_duration_locked`. Schema/calendar validation returns 422.

## Verification

`tests/test_workout_planning.py` covers profile counts, estimates, bounds,
legacy/new-account defaults, old onboarding clients, explicit save/apply scope,
protected history/programs, custom coverage, multiweek rollback, scheduled
boundaries, legacy duration and skip policy, and an actual two-connection
session-start/application race. Existing preferences, onboarding, week, program,
logging, resume, arrangement, Coach and profile/export suites provide regressions.
