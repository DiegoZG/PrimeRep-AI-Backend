# Exercise catalog authoring and review

## Current delivery

The source-controlled target contains **200 unapproved drafts**: all 41 canonical migration IDs and 159 proposed additions, arranged in eight 25-exercise batches. The earlier estimate of 43 was not supported by the canonical migration or the 41-record content file. Private exercises and database fixture rows are excluded.

The 159 new entries have individually authored setup, motion, cue and mistake text. Family files supply restrained benefits and beginner guidance. The 41 existing entries retain legacy instruction wording for comparison; its provenance and technique review remain unknown. None of these files constitutes trainer or publisher approval.

All drafts deliberately set `generation_eligible: false`. The trainer must explicitly approve generator eligibility and tracking/load conventions before publication. Publishing a legacy replacement while leaving this flag false removes that exercise from future personalized generation; it does not change existing plans or history. Do not batch-publish the 200 drafts unchanged.

## Readable packets

- [Batch 1: canonical exercises 1–25](catalog-review/batch-01.html)
- [Batch 2: remaining canonical exercises and first new drafts](catalog-review/batch-02.html)
- [Batch 3](catalog-review/batch-03.html), [Batch 4](catalog-review/batch-04.html)
- [Batch 5](catalog-review/batch-05.html), [Batch 6](catalog-review/batch-06.html)
- [Batch 7](catalog-review/batch-07.html), [Batch 8](catalog-review/batch-08.html)

Open these HTML files locally in a browser. They include instructions, classifications, equipment, load interpretation, source claims and review uncertainties. For an imported revision, `exercise_catalog.py review-packet` additionally shows the exact immutable revision hash, a diff from published content and staged media status.

```sh
PYTHONPATH=. .venv/bin/python scripts/catalog_build.py --inventory
PYTHONPATH=. .venv/bin/python scripts/catalog_build.py --batch 1
PYTHONPATH=. .venv/bin/python scripts/catalog_build.py --batch 1 --packet
PYTHONPATH=. .venv/bin/python scripts/catalog_inventory.py --database
PYTHONPATH=. .venv/bin/python scripts/exercise_catalog.py validate scripts/data/catalog/batch-01.json
PYTHONPATH=. .venv/bin/python scripts/exercise_catalog.py import scripts/data/catalog/batch-01.json --operator ACTUAL_AUTHOR --dry-run
```

Remove `--dry-run` only when importing the reviewed draft batch into the intended environment. Importing creates invisible drafts; it does not publish new exercises or replace existing text. Import output supplies revision IDs and hashes for the separate submit, review, publish and rollback operations.

For each imported revision, submit its exact hash and generate the packet the trainer will review:

```sh
PYTHONPATH=. .venv/bin/python scripts/exercise_catalog.py submit REVISION_ID --hash CONTENT_HASH --dry-run
PYTHONPATH=. .venv/bin/python scripts/exercise_catalog.py submit REVISION_ID --hash CONTENT_HASH
PYTHONPATH=. .venv/bin/python scripts/exercise_catalog.py review-packet REVISION_ID --output /private/path/to/review.html
```

After the trainer has actually reviewed that packet, import their real decision using the versioned artifact format below. Then obtain and import Diego's separate publication decision for the same hash. Do not create positive decisions just to advance the workflow. The latest trainer decision must be approved, and the publisher's approval must follow it; a new trainer review requires a new publisher review.

```sh
PYTHONPATH=. .venv/bin/python scripts/exercise_catalog.py review-import /private/path/to/actual-trainer-review.json --dry-run
PYTHONPATH=. .venv/bin/python scripts/exercise_catalog.py review-import /private/path/to/actual-trainer-review.json
PYTHONPATH=. .venv/bin/python scripts/exercise_catalog.py review-import /private/path/to/actual-publisher-review.json --dry-run
PYTHONPATH=. .venv/bin/python scripts/exercise_catalog.py review-import /private/path/to/actual-publisher-review.json
PYTHONPATH=. .venv/bin/python scripts/exercise_catalog.py publish REVISION_ID --hash CONTENT_HASH --operator ACTUAL_OPERATOR --release-id RELEASE_ID --dry-run
```

Only after checking the dry-run result, remove its `--dry-run` to publish. Use `report` to inspect catalog and review status. To restore a previously published revision that still has valid approvals, run `rollback PREVIOUS_REVISION_ID --hash PREVIOUS_CONTENT_HASH --operator ACTUAL_OPERATOR --release-id ROLLBACK_RELEASE_ID --dry-run`, then repeat without `--dry-run`. Rollback changes the catalog projection, not saved workout history. The saved legacy snapshot supports comparison, but is not a reviewed rollback target; the CLI restores previously published, approved revisions only. Never guess a hash or represent legacy content as trainer-approved.

## Research and unresolved decisions

Sources were accessed on 2026-09-20. Direct ACE instructional pages informed limited movement-family facts; Concept2's own technique pages informed rowing and SkiErg references. Each entry states exactly what its cited source supports. A related exercise page is **not evidence that a variant's entire technique, muscle classification or equipment setup was verified**. Variants have explicit trainer-review notes, including requests to add a directly applicable source where necessary.

The facts and original prose do not grant permission to reuse source photographs, video or wording. No vendor media or catalog has been imported. Reviewers should replace the 41 legacy descriptions where provenance or overly strong claims remain uncertain.

Known unresolved requirements:

- Anchored band chest press, standing row and Pallof press need a rated anchor that currently lacks an equipment ID. Add the appropriate equipment item or revise the exercise before approving it for generation.
- Cable attachments, machine reverse-fly compatibility and supports must be verified on the actual apparatus.
- Strength prescriptions remain owned by programs; content drafts contain no universal sets/reps/rest prescription.
- Timed planks and the five retained cardio exercises are reference-only for this phase.
- No filmed/AI pilot footage or human decisions have been supplied.

`catalog_inventory.py` reports normalized name/alias collisions, canonical coverage, missing text, invalid equipment references, duplicate candidates and aggregate database counts. Database inspection runs a read-only transaction and never outputs private exercise names, notes, owner IDs or credentials.

The target avoids counting equipment-model variants as separate movements: three proposed plate-loaded versus selectorized duplicates were replaced with a squeeze press, heel-elevated goblet squat and cable pull-through. Listed grip/stance/loading variations remain proposed identities for trainer verification; 200 is an editorial target, not a claim of 200 approved exercises.

## Editing and consistency

The authoring sources are `scripts/data/catalog/new-drafts.psv`, `families.json` and the legacy source file. `catalog_build.py` deterministically compiles them into the schema used by the publication CLI. After an editorial change, regenerate the affected batch and packet and run `tests/test_catalog_manifest.py`. Never edit an already submitted database payload; import a new revision and obtain reviews for its new hash.

Publication approval must be supplied by Diego or the designated publisher after a qualified trainer's review. The tooling does not infer approval from filenames, generated prose, technical validation or this document.

## Versioned review artifacts

Record actual signed-off decisions in a private version-controlled review repository or another approved record store. `exercise_catalog.py review-import PATH_TO_ACTUAL_REVIEW.json --dry-run` validates before import; remove `--dry-run` to append the decision. The artifact has exactly these fields:

```json
{
  "schema_version": 1,
  "subject_type": "content",
  "subject_id": "REPLACE_WITH_REVISION_OR_ASSET_ID",
  "subject_hash": "REPLACE_WITH_EXACT_REVIEW_HASH",
  "reviewer": "REPLACE_WITH_ACTUAL_REVIEWER_IDENTITY",
  "role": "trainer",
  "decision": "REPLACE_WITH_APPROVED_OR_REJECTED",
  "comments": "REPLACE_WITH_ACTUAL_REVIEW_FINDINGS",
  "reviewed_at": "REPLACE_WITH_ACTUAL_TIME_WITH_TIMEZONE"
}
```

This intentionally invalid template cannot be imported as an approval. Use `subject_type: media` for an asset, `role: publisher` for Diego's decision, and actual `approved` or `rejected` values. The subject hash must match the exact reviewed revision or media metadata. The CLI records the artifact digest for idempotent replay; altered content requires a new reviewed hash.
