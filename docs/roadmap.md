# PrimeRep roadmap

Current agreed roadmap, September 24, 2026. Phase names supersede older workspace instructions that described unfinished functionality already shipped.

| Part | Outcome | Status |
| --- | --- | --- |
| 4 | Durable workout completion, correction/history, notes, canonical summaries, account isolation | Completed |
| 5 | Core screens, password recovery, legal records, truthful UI, sharing, interaction audit | Completed |
| 6 | Real Explore catalog, private programs, activation, custom exercises | Completed |
| 7 | Private Coach feed, actionable guidance, timeline filters, preferences-aware targets | Completed |
| 7.5 | Owned exercise-content foundation, catalog expansion, review/publication pipeline, original media pilot | Engineering delivered; human approvals and original media remain pending |
| 8 | Profile improvements, support, and data portability | Merged; production release checks remain |
| 9 | Production readiness and observability | Planned |
| 10 | HealthKit and Health Connect integration | Planned |
| 11 | Subscriptions and entitlements | Planned |
| 12 / MVP+ | Camera-based gym-machine identification, exercise guidance/video, and questions | Planned |

Part 9 follows [the production readiness and observability plan](part9-production-readiness-plan.md). It verifies workout recovery and sync, deploys with production configuration, adds privacy-safe monitoring, and rehearses backup and rollback before a release decision.

Part 7.5 is specified in [the approved implementation plan](exercise-content-foundation-plan.md). A separate engineering milestone delivers tooling and drafts; publication requires actual trainer/owner approval. Continue content expansion and media production as an ongoing track after this foundation.

Part 8 follows [the approved profile/support/export plan](part8-profile-support-export-plan.md), with the supplied designs adapted to PrimeRep's purple/cyan theme. Backend deployment and its private-avatar migration precede the mobile release. Profile edits, real workout milestones, support email drafts, and authenticated personal archives are included; social sign-in, verified email changes, camera capture, public profiles, support conversations, and import remain deferred.

## Product focus: a trustworthy, actionable training loop

PrimeRep should compete through a sharper experience, not a longer feature list: give strength trainees a workable plan, make the next workout effortless, explain progress in practical terms, and let them retain control when real life changes the plan. The loop is **plan → perform → understand progress → adjust**. This is a product direction, not a claim that competing apps lack these capabilities. The September 2026 comparison of [Gravl](https://apps.apple.com/us/app/gravl-ai-personal-trainer/id6450921637), [STNDRD](https://apps.apple.com/us/app/stndrd-workout-fitness-plans/id1573298047), and [Hevy](https://apps.apple.com/us/app/hevy-workout-tracker-gym-log/id1458862350) found strong adaptive guidance, structured programs, and quick logging respectively; individual reviews and requests are useful signals, not representative proof of a missing feature.

Prioritize the following without changing the numbered phase order:

1. **Part 9 — trust first.** Make starting, logging, resuming, finishing, and synchronizing workouts reliable and observable. Do not let an expanded dashboard obscure incomplete or unsynced workout data.
2. **After the Part 9 baseline — Home dashboard refinement.** Home should answer “What do I do now?” Keep the next workout or active-session continuation as its primary action. Replace the under-filled space below Quick Stats with at most one relevant, actionable Coach alert and one compact trend from real logged performance; show useful first-workout and week-complete states. Remove the top-right star shortcut that merely duplicates the Coach tab. Deep-link to Coach, History, or the program for details rather than reproducing their feeds on Home. Design loading, empty, error, accessibility, and compact-layout states before implementation.
3. **Across Coach and workout logging — explain practical guidance.** Make target changes understandable, use loadable weights and the user's units/equipment, and make unavailable-equipment substitutions explicit and reversible. Avoid unsupported recovery scores or claiming that rules-based guidance is AI. Scope any new contracts separately.
4. **Ongoing Part 7.5 — quality before volume.** Publish trainer-reviewed instructions and useful demonstrations through the existing approval workflow. Let users inspect instructions or approved media before choosing a replacement; do not present unreviewed drafts or placeholder demonstrations as finished guidance.
5. **Later candidate work, subject to user evidence.** Explore temporary different-gym/no-equipment preparation, machine-specific load history, and more nuanced unilateral tracking in their workout flows—not as permanent Home cards. Do not expand social feeds, nutrition, public APIs, or broad integrations merely to match competitor checklists.

Validate the direction with a small cohort before committing to larger additions: measure first-workout completion, return and workout completion in week two, sessions finished without lost data, and whether users understand and accept recommendations. Interview people who stop using the app. Use those results to scope the Home iteration and later candidates, without inventing engagement or performance claims.

Production readiness includes a verified sending domain, trusted HTTPS password-reset route, delivery checks, legal approval, production configuration, and privacy-safe reliability metrics (including durable workout synchronization). Device-health and camera features must be added through their own scoped plans.

A public developer API and third-party commercial API product are deferred. Vendor catalogs are optional future evaluations, not the source of PrimeRep's identity or taxonomy. Large-scale original media production follows pilot quality/cost review. No paid production service is authorized by this roadmap alone.
