# Part 8 verification and rollout

Recorded September 22, 2026. Implementation uses `feat/profile-support-export`; backend deploys before the corresponding frontend. Install the Pillow requirement and upgrade Alembic to `t5u6v7w8x9y0` first. The accepted scope is in [the implementation plan](part8-profile-support-export-plan.md), and profile/photo contracts are in [Profile API](profile-api.md).

## Backend checks

- Profile-focused tests: 24 passed. Onboarding/account-deletion regressions: 66 passed.
- Full backend suite: **457 passed, 5 failed**. Each failure was reproduced on unchanged main, rather than treated as a passing suite:
  - `tests/test_onboarding_workout_contract.py::test_full_body_duration_growth_adds_complementary_exercises`
  - `tests/test_workout_templates.py::test_deactivation_rejects_arbitrary_historical_and_future_dates`
  - `tests/test_workout_templates.py::test_scheduled_cancel_requires_a_valid_caller_local_date`
  - `tests/test_workouts_next.py::test_workout_rotation_upper_lower`
  - `tests/test_workouts_next.py::test_workout_default_split_preference`
- After the final canonical-weight export fix, the focused profile, portability, and security run passed **75 tests**. The full-suite result above predates that final fix; it is not a claim of a fresh zero-failure full run.
- Disposable-database migration upgrade/downgrade/upgrade passed, preserving the sentinel user ID, name, and onboarding body values. Avatar storage and internal per-field guards were restored on re-upgrade.
- `git diff --check` passed. Testing and migration databases were isolated from the development database.

Local evidence: `/private/tmp/primerep-part8-full-backend-final-review.log`, `/private/tmp/primerep-part8-main-baseline-review.log`, and `/private/tmp/primerep-part8-main-duration-review.log`. These temporary logs are not deployment artifacts.

## Integrated checks

- The full Part 8 Maestro flow passed on iPhone 16 Plus: profile identity save/discard, achievements, FAQ navigation, and export-screen checks.
- Profile was visually verified in light and dark themes.
- Native photo pick/crop/save/reopen persisted; picker cancellation retained the unchanged editor and disabled Save state.
- Native ZIP sharing completed via Save to Files. Browser ZIP download also succeeded. The inspected archive contained `account.json`, `workouts.csv`, `sets.csv`, `profile-photo.jpg`, and `README.txt`, with 12 workouts and 36 set rows. Its photo was a 512×384 JPEG without EXIF. Credential and internal AI-fact fields were absent.
- Browser Profile and Home loaded against the isolated fixture API; Home returned three workouts.

## Remaining release checks

Configure and verify a real monitored support inbox. Missing/example support addresses deliberately fail frontend production validation. Real mail-app behavior, Android, photo permission-denial flows, and large-text checks remain manual; they are not covered by the iOS results above. No support messages were sent during verification.

The fixture seeder requires `APP_ENV=test` and a database name beginning `primerep_part8_ui_`. Its account and workout history are synthetic. Do not use it against production or an ordinary development database.
