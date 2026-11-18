# Three-machine scanner pilot: review and evaluation

This packet prepares review; it does not approve exercise advice, media rights, recognition accuracy or release. The existing catalog already has the three editorial drafts below. They remain unapproved; do not generate duplicate instructions or publish them unchanged to make the scanner look complete.

| Machine → canonical exercise | Existing draft | Human-readable packet |
| --- | --- | --- |
| `lat_pulldown_cable` → `lat_pulldown` | [batch-01.json](../scripts/data/catalog/batch-01.json) | [Lat Pulldown](catalog-review/batch-01.html#lat_pulldown) |
| `row_cable` → `seated_row` | [batch-01.json](../scripts/data/catalog/batch-01.json) | [Seated Cable Row](catalog-review/batch-01.html#seated_row) |
| `leg_extension_machine` → `leg_extension` | [batch-02.json](../scripts/data/catalog/batch-02.json) | [Leg Extension](catalog-review/batch-02.html#leg_extension) |

## Readiness and a focused review file

Run from the backend root using the intended environment. Default readiness reads local draft metadata only; database inspection is opt-in, read-only and reuses the scanner's public catalog projection. It reports whether each expected equipment/exercise link, published instructions and currently approved video exists. Draft text, legacy URLs or expired/withdrawn approvals do not count. It prints no account IDs, photos, credentials or provider output and makes no AI request.

```sh
.venv/bin/python -m scripts.equipment_scan_pilot readiness
.venv/bin/python -m scripts.equipment_scan_pilot readiness --database
.venv/bin/python -m scripts.equipment_scan_pilot review-manifest > /private/tmp/primerep-three-machine-review.json
.venv/bin/python -m scripts.exercise_catalog validate /private/tmp/primerep-three-machine-review.json
.venv/bin/python -m scripts.exercise_catalog import /private/tmp/primerep-three-machine-review.json --operator ACTUAL_AUTHOR --dry-run
```

The extracted manifest contains only the existing three draft entries; extraction is not review or publication. Check the configured database before any mutating catalog command. The report's `catalog_status: ready` means those catalog requirements are present; it cannot establish recognition accuracy or deployment readiness.

## What the reviewers need to decide

- A qualified trainer reviews each actual apparatus, setup/support and attachment requirements, steps, cues, common mistakes, beginner guidance, muscle classifications and machine load interpretation. Confirm which manufacturer/model the guide covers and what it does not cover.
- The draft source claims are limited. Existing legacy prose has provenance gaps; check strong or unsupported claims and edit the draft before approval. `generation_eligible: false` is a deliberate pending decision: publishing unchanged can remove that exercise from future personalized generation. Review eligibility explicitly; existing workout history must stay intact.
- A separate designated publisher reviews the exact trainer-reviewed content hash afterward. Trainer and publisher identities must be real and distinct. A new draft or trainer decision requires a new publication review; old approvals do not carry over.
- Videos require documented reuse/publication rights, participant permissions where applicable, technical validation, trainer review of the actual clip and separate publisher approval. A source article, publicly visible vendor video or successful transcode is not a media license or technique approval.

Manufacturer references can help a reviewer identify the actual machine: [Life Fitness lat pulldown](https://www.lifefitness.com/en-us/catalog/strength-training/selectorized/axiom-series-lat-pulldown), [seated row](https://www.lifefitness.com/en-us/catalog/strength-training/selectorized/circuit-series-seated-row), [combined lat/row](https://www.lifefitness.com/en-us/catalog/strength-training/selectorized/axiom-series-lat-pulldown-low-row), and [combined curl/extension](https://www.lifefitness.com/en-us/catalog/strength-training/selectorized/axiom-series-seated-leg-curl-extension). These references grant no image/video reuse permission. Combined stations are ambiguity cases until their supported configuration has a verified catalog mapping; do not force them into a single-purpose identity.

## Existing publication workflow

Use the [content review workflow](catalog-content-review.md) for actual import, submission, signed review artifacts and publication. After deliberately importing the draft (without `--dry-run`) into the intended database, its output supplies the real revision ID and content hash. Submit that exact revision, then generate its review packet. Dry runs do not persist an import or submission.

```sh
.venv/bin/python -m scripts.exercise_catalog submit REVISION_ID --hash CONTENT_HASH --dry-run
.venv/bin/python -m scripts.exercise_catalog review-packet REVISION_ID --output /private/tmp/actual-revision-review.html
.venv/bin/python -m scripts.exercise_catalog review-import /private/path/to/actual-trainer-review.json --dry-run
.venv/bin/python -m scripts.exercise_catalog review-import /private/path/to/actual-publisher-review.json --dry-run
.venv/bin/python -m scripts.exercise_catalog publish REVISION_ID --hash CONTENT_HASH --operator ACTUAL_OPERATOR --release-id RELEASE_ID --dry-run
```

The real submission and actual trainer approval must be persisted before importing the subsequent actual publisher decision. Only remove dry-run flags after the corresponding real review and intended-environment check. Never write approval artifacts on a reviewer's behalf merely to advance the workflow.

For a rights-cleared original video, use the existing [media operations](media-operations.md) staging and publication workflow; no new uploader is needed:

```sh
.venv/bin/python -m scripts.upload_local_footage stage --dir /absolute/path/to/footage --only EXERCISE_ID --operator ACTUAL_OPERATOR --production-method filmed --rights-file /private/path/to/publication-permission.txt --variant front-three-quarter --dry-run
.venv/bin/python -m scripts.exercise_catalog media-review ASSET_ID --hash APPROVAL_HASH --reviewer ACTUAL_TRAINER --role trainer --decision approved --comments "Actual technique findings" --dry-run
.venv/bin/python -m scripts.exercise_catalog media-review ASSET_ID --hash APPROVAL_HASH --reviewer ACTUAL_PUBLISHER --role publisher --decision approved --comments "Actual publication decision" --dry-run
.venv/bin/python -m scripts.upload_local_footage publish --asset-id ASSET_ID --hash APPROVAL_HASH --operator ACTUAL_OPERATOR --release-id RELEASE_ID --dry-run
```

These approval examples are conditional on actual approvals, not instructions to fabricate them. Use `rejected` and the actual reason when appropriate. Staging requires configured private storage; actual publication requires the existing technical, rights, independent review and approved-content gates.

## Real-photo evaluation remains not assessed

[The evaluation manifest](../scripts/data/equipment-scan-pilot-evaluation.json) is a collection checklist, not a supplied dataset or completed benchmark. It plans three views for each pilot machine plus pull-up-bar, leg-curl, combined-station, unrelated-object, unclear-crop and illustration cases. A leg-curl photo should map to its own existing supported identity, not leg extension. Unsupported, unrelated, unclear and unverified combined-station cases should abstain. The illustration is a separate rejection check and can never increase real-photo accuracy.

Before recording results, supply genuine consented or permission-cleared photos, private provenance/permission references, actual ground-truth reviewer identity and decisions, then a versioned prediction-run reference, actual model, recognition revision and catalog version. Use private copies of the manifest for real materials; do not commit personal gym photos or permission documents. The current file contains no photos, approvals or predictions. No paid evaluation or new provider upload is authorized by this packet.

The offline evaluator reads only supplied JSON records. It does not open image files, fetch URLs, call a model or verify an operator's provenance claims. Syntactically valid equipment IDs are not proof of membership in the recorded catalog version; the reviewer must verify that mapping. `predicted_equipment_ids: null` means not run; `[]` means recorded abstention. Missing predictions, rights or reviews remain explicit pending cases. A correct candidate accompanied by a wrong candidate fails the exact-set check. Reported values summarize those records only; they are not independent proof of model accuracy.

```sh
.venv/bin/python -m scripts.equipment_scan_pilot evaluate scripts/data/equipment-scan-pilot-evaluation.json
```

With the supplied checklist, the result must be `not_assessed`, with no accuracy percentage. Even a completed recorded run returns `release_ready: false`: inspect wrong-machine results, false acceptances, false abstentions, coverage and all remaining product/privacy/device/content gates before a separate release decision. Resolver observation fixtures and mocked simulator flows remain engineering tests, not real-photo recognition evidence.
