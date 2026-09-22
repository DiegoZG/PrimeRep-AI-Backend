# Part 7.5 — Exercise Content and API Foundation

Approved implementation plan, September 20, 2026. Backend merges before the paired frontend branch, `feat/exercise-content-foundation`.

## Outcomes and milestones

Build PrimeRep's own versioned catalog, operator review workflow, and media foundation. Target 200 distinct canonical exercises across gym and home strength training, counting existing canonical exercises and excluding private exercises. Engineering and review-ready content are deliverable independently of publication. Actual trainer review, Diego's publication approval, and approved media assets are required before claiming publication complete.

Astra assists with research and original drafts in batches of at most 25. A qualified trainer reviews technique and classifications. Diego approves publication. Operators use readable HTML review packets and CLI tooling. Exercise videos play in the app. An original filmed-versus-AI pilot precedes a larger 25-exercise production effort. Vendor import, a public developer API, and an admin website are deferred.

## Stable identity and compatibility

- Preserve exercise/equipment IDs, ownership, favorites, existing workout plans, active snapshots, and completed history.
- Retain existing exercise types and muscle values; add movement pattern, resistance modality, difficulty, laterality, and load interpretation separately.
- Alternate names are aliases. Meaningful movement variations have distinct exercise identities. Camera/model variants are media assets of one exercise.
- Prescriptions remain contextual program/generator data rather than universal exercise properties.
- Existing exercises remain the published read model. Legacy text remains readable with unknown review provenance; never fabricate approval records.
- Private custom exercises retain their owner-controlled workflow. New canonical drafts must be excluded from catalog, search, favorites, substitutions, templates, generation, and Q&A.

## Versioning and publication

Add immutable submitted exercise revisions, review records bound to revision hashes, audited publication records, and media asset records. Revisions include equipment, classifications, ordered instructions, cues, mistakes, benefits, beginner guidance, aliases, source references, and explicit generation eligibility.

Workflow: Draft → Submitted → Trainer approved → Publication approved → Published. Editing submitted content creates a new revision requiring new approvals. Both approvals must name the exact revision hash. Publication and rollback update the read model transactionally, retain previous revisions, and record operator/time/catalog-release information.

CLI operations: import, validate, submit, record review, publish, rollback, report. Support dry runs and idempotent imports. Explicitly identify reviewers/operators; do not generate approvals. Replace filename-based review guards and route legacy content/media publishing scripts through the new services.

## Equipment and load semantics

Audit equipment names/references, preserve IDs and user selections, and add aliases or genuinely missing entries. All required equipment must remain available. Describe one implement, a pair, total barbell load, machine setting, bodyweight, added load, or assistance without reinterpreting historical weights. Carry reviewed load metadata into new snapshots and Coach guidance, including single-versus-paired dumbbells. Keep compact weight display with optional detail. Unsupported tracking requirements exclude an exercise from new generated strength workouts while allowing reference browsing.

## API contracts

Extend existing endpoints:

| Endpoint | Additive behavior |
| --- | --- |
| `GET /v1/exercises` | Pagination metadata, catalog version, concise published classification/load metadata |
| `GET /v1/exercises/{id}` | Structured published content, content version, approved media descriptors |
| `GET /v1/explore/search` | Independent program/exercise pagination while preserving result arrays |
| `GET /v1/equipment` | Catalog version and optional aliases; preserve IDs/fields |
| Existing Q&A | Ground new responses in the published revision; record that revision internally |

Keep snake-case exercise contracts and current camel-case contracts elsewhere. Project structured instructions into the four legacy text fields and selected media into legacy URLs. Keep review identities, draft content, production records, and rights documents private. Reuse exact/prefix/substring/trigram ranking with stable ID tie-breaking. Mobile pages contain 50 items; the backend request cap remains 200.

## Phase 0 — Baseline and inventory

Record baseline checks before implementation. Inventory canonical seeds and local database where available; separate private/test rows. Reconcile 41 source-controlled content records with canonical exercise rows. Audit aliases, missing text, equipment validity, media URLs, and duplicate movements. Build the 200-exercise coverage manifest across push, pull, squat, hinge, unilateral legs, core, accessories, and common resistance modalities. Prioritize system programs, current generator coverage, and home alternatives. Preserve cardio entries; timed/distance tracking is deferred.

Acceptance: a reproducible inventory explains existing IDs and proposed additions without treating aliases as new exercises.

## Phase 1 — Storage and publishing controls

Add migrations and safe backfills for revisions/reviews/publications/media/additive metadata. Implement shared validation/publication services and operator CLI. Preserve user references. Ensure a changed approved field requires re-review and a failed publish leaves the current publication intact.

## Phase 2 — Original content and review packets

Deliver schema-validated original drafts for all 200 targets in batches of at most 25. Include practical instructions, cues, mistakes, beginner guidance, classifications, equipment, aliases, and research references. Record source title/URL/access date and claims informed; research citations are distinct from content/media reuse rights. Prefer primary instructional sources, manufacturers, and recognized training organizations. Mark uncertainties for trainer review. Never invent citations or review history.

Produce HTML packets with differences, source links, classification validation, and associated media. Import real trainer/Diego decisions through versioned review artifacts. Every target is either actually approved/published or explicitly awaiting review. Missing video does not block approved text publication.

## Phase 3 — Discovery and workout integration

Apply publication/ownership rules across every consumer. Paginate category lists and the exercise picker beyond current 100-item limits; retain account fencing and reset pagination on query/filter/account changes. Keep complete Favorites objects. Reviewed substitutions consider movement compatibility, muscles, and owned equipment; preserve legacy rules where metadata is absent. Keep explicit activation review and duplicate protection. Admit only explicitly eligible, supported exercises to new generation. Do not regenerate existing plans on catalog publication.

Correct numeric exercise-ID declarations, authenticated loading/account fencing in the workout information sheet, and private custom instruction rendering. Acceptance: exercise 101+ can be found, favorited, added to a program, and opened during a workout; drafts and other users' exercises cannot.

## Phase 4 — Media pipeline and playback

Extend the existing uploader using immutable hash keys and idempotent S3-compatible operations. Cloudflare R2 is the documented production default. Keep staging/originals/production records private and publish only approved derivatives. Validate with ffprobe; use ffmpeg for H.264 MP4 and posters. Use 9:16 masters with full movement visibility and uncropped 720p playback derivatives. Require technical validation, trainer approval, and documented rights. Record variants; publish one default per exercise.

Configure a custom media domain, content types, caching, and browser CORS. Use SDK-compatible `expo-video`, `useVideoPlayer`, `VideoView`, and status events. Share playback between detail and workout information: poster/tap-to-play/native controls/fullscreen; pause on navigation, sheet dismissal, and backgrounding; contain the full frame. Keep text readable through loading/error/unavailable states; allow poster failure. Retain external opening only for legacy non-playable webpage URLs. Do not promise offline video or bundle the catalog.

References: [R2 public buckets](https://developers.cloudflare.com/r2/buckets/public-buckets/), [Expo SDK 54 video](https://docs.expo.dev/versions/v54.0.0/sdk/video/).

## Phase 5 — Pilot and operations

Prepare matching original filmed/AI briefs for `push_up`, `dumbbell_shoulder_press`, and `squat`: short clips with two or three controlled repetitions, neutral setting, no voiceover. Track production time/cost/revisions/tool versions/permissions. Trainer review checks movement accuracy, joints/equipment, visibility, and instructional agreement. Technical checks never substitute for technique approval. Deliver a comparison report that marks missing candidates honestly and a ranked 25-exercise production backlog; larger production remains a subsequent decision.

Report catalog coverage, pending reviews, approved media, validation failures, and publish/rollback failures. Add aggregate catalog timing/error/zero-result logs through existing patterns; exclude raw searches, questions, notes, credentials, and identity. No general analytics SDK.

## Verification and rollout

Backend: disposable-database migration upgrade/downgrade/upgrade; ID/reference preservation; immutable revision/approval/transaction/idempotency tests; draft/private/archive isolation; legacy API projections; search/pagination; equipment/generation/substitution compatibility; historical snapshot preservation; load semantics; Q&A provenance; media validation/staging/approval/failure/retry. Run focused and full suites.

Frontend: fixtures beyond items 100/200; query/filter/account races; structured/legacy/private content; detail/sheet parity; themes/accessibility/fullscreen/dismissal/failures; Favorites/programs/Coach/logging regressions. Run lint/typecheck/UI-action audit/web export/Maestro validation and available simulator flows, plus diff checks in both repos.

Deploy backward-compatible backend before frontend. Publish only genuinely approved batches, then approved pilot assets, then verify deployed content/media. Rollback switches to an approved revision and never deletes IDs or rewrites history.

## External prerequisites and boundaries

Diego arranges the trainer and actual review decisions. Filmed media requires participants and publication permissions. Paid AI generation requires a selected service, access, and an explicit spending limit; initial engineering produces briefs and ingestion tooling without purchases. Production needs storage credentials and a media domain; local fixtures support development. English only initially. Full 25-video production, selectable angle/model variants, translations, public API products, and machine recognition remain later work. The delivery report must distinguish completed engineering/drafts from outstanding approval/assets.

## Recorded pre-change baseline

- Backend main: `88e207c7ae9574be774bdd1f9928117fa72a5d55`.
- Frontend main: `bfcc70fabf0919dc4853718c041774e31a4e93c7`.
- Both repositories were clean and `git pull --ff-only origin main` reported up to date.
- Frontend lint passed. Typecheck had the two existing `TimingConfig.delay` errors in `components/animated-card.tsx`, lines 30 and 34.
- Existing migrations applied successfully to isolated database `primerep_part75_baseline_20260920` at revision `r3s4t5u6v7w8`; no development database migrations or test writes were performed.
