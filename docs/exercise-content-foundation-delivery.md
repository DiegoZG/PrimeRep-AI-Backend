# Part 7.5 delivery report

Date: 2026-09-20. Branch: `feat/exercise-content-foundation` in both repositories.

## Delivery boundaries

The engineering foundation and review-ready editorial drafts are separate from publication. No trainer or Diego approval has been invented, and no production exercise or media has been published by this implementation.

| Milestone | Delivered state |
| --- | --- |
| Catalog target | 200 schema-valid English drafts: 41 existing canonical identities and 159 proposed additions |
| Readable review material | Eight batches of at most 25, HTML review packets, source links and explicit uncertainty |
| Human content approvals | 0 supplied; all replacement/new content awaits actual review |
| New published exercises | 0; existing legacy content remains available |
| Media foundation | Private staging, immutable hashed assets, technical derivatives/posters, separate media reviews, audited publish/rollback |
| Actual production demonstrations | 0 supplied/approved; synthetic technical fixtures are not exercise demonstrations |
| Filmed-versus-AI pilot | Original briefs for push-up, dumbbell shoulder press and squat, comparison worksheet, ranked 25-exercise backlog |

See [inventory](catalog-inventory.md), [content review workflow](catalog-content-review.md), [media operations](media-operations.md), [pilot](media-pilot.md), [approved plan](exercise-content-foundation-plan.md), and [roadmap](roadmap.md).

## Independent backend review

The fresh verification pass reviewed centralized publication/ownership predicates, exact-hash approval checks, immutable audit records, publication transactions, legacy provenance, snapshot load metadata, pagination/ranking, and retired direct publishing paths. It also closed these integration gaps:

- Added a usable `media-review` CLI and strict, versioned `review-import` artifacts. Replaying an already imported approval cannot overwrite a later rejection.
- Added common equipment aliases without changing IDs or saved equipment selections.
- Excluded newly reviewed duration/distance-only entries from program creation, activation and substitutions while leaving their reference detail available and preserving legacy behavior.
- Applied the centralized exercise visibility check during activation preview.
- Rejected unknown/noncanonical owners/sources in catalog and media import services.
- Protected revision/media identity and creation timestamps in addition to content hashes, and removed misleading help text from the retired direct-upsert command.

## Backend verification

All database verification used disposable databases, not the developer's application database.

- Focused catalog, manifest, technical media and uploader suites: 37 passed before the final additional unknown-source regression test; all 38 of these tests passed as part of the final full suite.
- Migration `s4t5u6v7w8x9 → r3s4t5u6v7w8 → s4t5u6v7w8x9`: passed repeatedly.
- Before/after SHA-256 comparisons matched for all old exercise fields, 97 equipment rows, exercise-equipment relationships, favorites, 373 stored week plans and 150 sessions in the populated test database. Additional comparisons matched equipment weights, session operations/feedback, all template graph tables and 125 immutable program activation records. Test/private fixture rows are not counted as canonical catalog content.
- Real FFmpeg verification covers a synthetic 9:16 master, deterministic H.264 720×1280 derivatives, and generated posters. It does not establish movement quality or publication rights.
- Static anti-pattern search found no remaining old content/media script path that directly publishes into exercise read fields outside the reviewed service. Legacy `upsert` exits with migration guidance.
- `git diff --check`: passed.

Final full suite: **417 passed, 2 failed** in 105.32 seconds. Both failures were independently reproduced from an unchanged `main` archive (base commit `88e207c`) against the separate disposable baseline database:

- `tests/test_workout_templates.py::test_supersession_and_deactivation_preserve_stored_historical_program_week`: expects HTTP 422 for future deactivation, receives 200.
- `tests/test_workouts_next.py::test_workout_ppl_split`: the Sunday next-workout ID is not in the current-week response.

These are pre-existing failures, not a green full-suite result. They remain visible release follow-ups; Part 7.5 introduced no additional failing test in this run.

## Frontend verification

Independent lint, UI-action audit, 11 behavioral regressions, Expo web export (56 routes), and frontend `git diff --check` passed. Typecheck has only the two unchanged `AnimatedCard` `TimingConfig.delay` failures at lines 30 and 34.

Live production-web verification used a separate `primerep_part75_ui_20260920` database and 250 clearly synthetic exercise rows: all five search pages loaded; item 201 was favorited/opened; a locally generated 3-second H.264 clip played to completion without error; navigating back removed its video element. The program picker independently loaded all five pages and selected item 201 into a temporary draft. No synthetic approval records were created.

Actual browser testing also identified pre-existing native-notification and SecureStore web bootstrap failures. Minimal platform guards and a memory-only web token module resolve these; browser reload requires login again. Native token storage is unchanged. Reviewed duration/distance-only exercises are disabled in the program picker with an explicit reference-only explanation.

iOS Maestro was attempted on the booted iPhone 16 Plus / iOS 18.5 but did not finish: Metro's development virtual environment ignored the isolated API override, then a cold-bootstrap timeout occurred after dotenv was disabled. Android was unavailable. Native playback/fullscreen, background/foreground, workout-sheet dismissal, both-theme visual checks and device failure/retry checks remain unverified manual release checks. Mocked lifecycle regressions and a successful web clip are not native-device proof.

The application's normal database was not migrated. Before testing the branch against that database, apply `alembic upgrade head`. All fixture/database checks here were isolated; test servers were stopped afterward and the developer's normal ports 8000/8081 were left running.

## Publication and production dependencies

1. A qualified trainer must review the exact submitted content hashes; Diego must then record publication decisions for those same hashes.
2. Three anchored-band drafts still need a resolved, rated-anchor equipment requirement. They must not be approved/published with that requirement unresolved. All drafts initially keep personalized generation disabled until eligibility is explicitly reviewed.
3. Actual filmed participants, releases/rights records, footage, trainer media review and publication approval are required. AI-video candidates additionally require the chosen tool, access and a spending limit; no paid production was started.
4. Production requires private staging and separate approved-delivery storage, scoped credentials, a custom HTTPS media domain, cache/content-type/CORS configuration and a live delivery smoke check.
5. Existing legacy content has unknown review provenance. Its migration snapshot is not an approval. Publishing new catalog content must not rewrite completed sessions, active snapshots or already generated plans.

## Rollout

Merge/deploy the backward-compatible backend first, then the frontend. Import and submit review batches through the CLI; publish only after real approvals. Publish media independently after technical, trainer and rights checks. Rollback changes approved content/media pointers without deleting exercise identities or rewriting user history.
