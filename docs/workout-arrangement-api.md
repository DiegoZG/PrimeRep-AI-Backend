# Session exercise arrangement

Deploy migration `a2b3c4d5e6f7` and the API before enabling client controls.
Existing clients keep working. The immutable `workoutSnapshot` remains provenance;
`effectiveWorkout` is session-owned and falls back to that snapshot on old rows.
`arrangementRevision` starts at zero. Changes never update the weekly plan or program.

All paths below are under `/v1/workouts/sessions/{sessionId}` and require the owner.

| Method/path | Body | Result |
| --- | --- | --- |
| GET `/exercises/{exerciseId}/replacement-candidates` | — | `{items: ExerciseOut[]}` using the existing snake_case exercise fields |
| POST `/exercises/{exerciseId}/replace` | `{replacementExerciseId, expectedRevision, clientOperationId}` | Full session |
| POST `/order/preview` | `{exerciseIds, expectedRevision}` | `{arrangementRevision, exerciseIds, targetsChanged, targetChanges, effectiveWorkout, message}` |
| PATCH `/order` | `{exerciseIds, expectedRevision, clientOperationId, applyRevisedTargets: false}` | Full session |

`exerciseIds` must be a complete permutation. Items retain their exercise identity;
consecutive blocks may repeat a block type to represent any interleaved order.
`targetChanges` contains `{exerciseId, fromBlockType, toBlockType, before, after}`.
The preview is read-only. Apply recomputes under the session row lock, shared with
set writes, so sets arriving after preview protect their targets too.

Replacement requires an in-progress session and no set ever recorded for the
exercise, including deleted sets. Pending local sets must sync before the client
offers replacement; the server cannot observe writes not yet sent by a device.
Candidates reuse the existing visible, active, compatible, owned-equipment rules
and exclude the effective workout. Archived originals return an explicit 422.
Sets/reps/rest are retained; only the replacement's own history supplies load,
and the old explanation is cleared. Set writes and feedback validate effective
membership. Replacement has no effect on completed history or future workouts.

Only known generated splits can offer target changes. The first original N main
slots define the order boundary. Eligible untouched accessory exercises entering
that boundary are paired with untouched main exercises leaving it. Promotion
requires the existing main exercise types; demotion allows supported generator
fallback types. Both must retain the canonical numeric prescription for their
current role and have no current or deleted sets. Pairs use existing main and
accessory prescriptions and each exercise's own history suggestion. The main
slot count stays N. Unpaired moves, uncertain provenance, isolation promotion,
user program targets and edited prescriptions keep their targets.

404 hides sessions belonging to another user. 409 reports revision, terminal
status, started-exercise and operation-reuse conflicts. 422 reports incompatible
replacement or invalid order. Exact operation retries return the current session,
even after a later edit; fingerprints include the entire submitted payload so
reusing an operation ID with a different payload returns 409. A stale revision
requires fetching the latest effective workout and explicit order review.
