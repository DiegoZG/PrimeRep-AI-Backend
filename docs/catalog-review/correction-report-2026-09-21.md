# Batch 1 editorial corrections — September 21, 2026

## Status and provenance

This is an AI-assisted correction pass, **not trainer or publication approval**. No database import, review, publication, equipment migration, workout regeneration or historical weight rewrite was performed. The supplied review attributed itself to Claude (Opus 5), without professional credentials. Its four favorable verdicts remain editorial opinions pending actual human review.

Reviewed original packet: `batch-01.html`, SHA-256 `ca4cdeaa2df0d9849f5c9158db0e570f5ddfb684a7b53fdbfaf8e668f50d30d4`. This hash was verified before regeneration. The original JSON/HTML remains in Git commit `7271f77`; current packets contain exact content fingerprints and expandable diffs against that commit.

## Completed corrections

- Replaced the family-benefit-as-cue fallback affecting all 41 legacy exercises with exercise-specific cues. The audit also found and separated ten shared cue sets among the 159 newer drafts.
- Added separate benefits and beginner guidance for the first 25 drafts; removed the dip's inherited pushdown description.
- Made simultaneous front raises and palms-forward simultaneous dumbbell curls agree with their bilateral records. Removed the dumbbell option from short-bar preacher curls and EZ-bar skull crushers.
- Removed the assertion that another cable-fly direction is inherently a mistake. Removed sled-specific catches from the selectorized leg-press draft and retained an explicit manufacturer-setup blocker.
- Proposed hip-extensor classification for deadlift and revised the flagged difficulty labels. These are draft classifications for human confirmation, not published changes or validated readiness thresholds.
- Added conservative setup/supervision language to bench, squat and skull-crusher drafts. Did not turn proposed bailout instructions, exact joint angles, contraindications or performance cutoffs into asserted guidance.
- Disambiguated the two ACE Chest Press citations using equipment and exercise number. Added directly relevant or explicitly limited references for incline press, cable fly, pull-up, reverse fly, pec deck and dip. Related sources are not represented as proof of all technique or machine details.
- Added cue/benefit duplicate validation, a cross-batch audit, manifest and per-exercise hashes, and before/after packets. No fake revision IDs or approvals were created.

## Questions still requiring decisions

| Area | Outstanding decision |
| --- | --- |
| Bench press, back/front squat, skull crushers | Qualified review of setup, spotter/safety requirements and failed-repetition handling; explicit equipment/profile eligibility rules before generation. |
| Pull-up and dip | Readiness and assisted/weighted tracking model; do not implement the suggested three-rep cutoff without validation. |
| Push-up | Review regression variants and their equipment; added load is not made equivalent to bodyweight logging. |
| Deadlift | Human confirmation of classifications and technique; keep generation disabled pending a product policy. |
| Cable crossover | Per-stack versus combined weight, pulley ratios and compatibility with existing logs. No numerical conversion has been applied. |
| T-bar row | Confirm the actual plate-loaded apparatus. The existing ID does not prove it is a free landmine. Decide whether logged load includes starting resistance; the old machine load profile remains pending that decision. |
| Machines | Obtain apparatus-specific seat, pad, support and stopping instructions. A fly/leg-press family citation does not validate these. |
| Exercise alternatives | Chest-supported rows/flys, grip changes and assistance require correct IDs/equipment; do not silently redefine a canonical exercise. |
| Clinical questions | No blanket shoulder/back exclusions, diagnoses or contraindications were inferred from AI suggestions. |
| Remaining source coverage | Family references remain contextual in later batches and several first-batch records; trainer/editor must supply direct coverage where needed. |

The reviewer-proposed 15/7/3 eligibility split is not encoded as approved eligibility. All 200 drafts remain `generation_eligible: false`. Do not publish legacy replacements merely to advance workflow: publishing them while false would remove them from future personalized generation.

## Reproduce and review

```sh
PYTHONPATH=. .venv/bin/python scripts/catalog_build.py --write --compare-ref 7271f77
PYTHONPATH=. .venv/bin/python scripts/catalog_build.py --audit
PYTHONPATH=. .venv/bin/pytest -q tests/test_catalog_manifest.py
```

Review the corrected [Batch 1 packet](batch-01.html), including each unresolved question and its expanded diff. A matching manifest/content hash identifies exactly what was reviewed. Database import/submit assigns revision IDs; only actual human decisions may be attached to them. Continue engineering roadmap work independently while this content track awaits review.

## Verification

- All eight regenerated batches retain 200 distinct IDs and generation-disabled status.
- Structural audit: zero cue/benefit duplicates, zero shared complete cue sets, zero ambiguous source titles.
- Catalog, editorial-manifest, media-pipeline and uploader regression suites: 41 passed against the isolated test database.
- `git diff --check` passed. No frontend or runtime schema migration is needed.
- Programmatic checks cover HTML escaping, exact import-compatible hashes and before/after diffs. Browser inspection of the local HTML was blocked by the browser URL policy; no new visual-review pass is claimed.
