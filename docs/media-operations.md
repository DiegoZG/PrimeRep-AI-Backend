# Original media staging, review and delivery

## Storage and local dependencies

Install FFmpeg (`ffmpeg` and `ffprobe`) and the backend dependencies. The footage tool keeps the existing S3-compatible client and uses ordinary AWS credential configuration; it never prints credentials.

Configure `ASSET_STAGING_BUCKET` as a private bucket with public access disabled. Configure `ASSET_BUCKET` as a different delivery bucket and `ASSET_CDN_BASE_URL` as its trusted HTTPS custom domain. Cloudflare R2 is the default deployment recommendation; its `r2.dev` endpoint is for development, not production delivery. See [Cloudflare public buckets](https://developers.cloudflare.com/r2/buckets/public-buckets/) and [CORS configuration](https://developers.cloudflare.com/r2/buckets/cors/).

For browser playback, allow `GET` and `HEAD` from the exact deployed app origin. Allow `Range` requests and expose `Content-Length`, `Content-Range`, `Accept-Ranges` and `ETag` where appropriate. Verify a real browser range request against the configured domain before release. Do not make the staging bucket public to solve a playback or CORS problem.

Original masters, production notes, releases and rights documents remain private. Public delivery contains only approved MP4 derivatives and JPEG posters. Content-addressed delivery keys have one-year immutable caching; replacement media gets new keys. Do not delete previous approved assets while published references or rollback needs remain.

## Stage footage

Use one original file per exercise, named after its stable ID, for example `push_up.mov`. Supply a 9:16 master with the entire person and equipment in frame. The validator accepts 1–120 second clips; pilot briefs target two or three controlled repetitions. Phone rotation metadata is respected.

```sh
PYTHONPATH=. .venv/bin/python scripts/upload_local_footage.py stage \
  --dir /absolute/path/to/footage \
  --operator ACTUAL_OPERATOR \
  --production-method filmed \
  --rights-file /private/path/to/publication-permission.txt \
  --variant front-three-quarter \
  --dry-run
```

Remove `--dry-run` to prepare and stage. For generated clips, use `--production-method ai_generated --model-version ACTUAL_PROVIDER_AND_MODEL_VERSION`; use `hybrid` for mixed production. Record the original production records separately and include their references in the supplied rights text.

Preparation probes the master, scales without cropping into a 720×1280 canvas, produces H.264/yuv420p MP4 at 30 fps with fast-start metadata, removes audio, generates a poster, probes the derivative again, and hashes each result. Outputs are uploaded only to private staging. Registration returns the asset ID and approval hash; it does not alter the exercise's visible image/video URLs.

The approval hash covers content, poster/storage keys, technical metadata, provenance and rights. Changing rights, the model version or the variant creates a separately reviewed asset even when the video bytes stay the same. Unchanged uploads and registrations are idempotent.

## Review and publish

Reviewers inspect the actual staged clip through an authorized private-storage viewing method. Do not treat a successful transcode as technique or publication approval.

```sh
PYTHONPATH=. .venv/bin/python scripts/exercise_catalog.py media-review ASSET_ID \
  --hash APPROVAL_HASH --reviewer ACTUAL_TRAINER --role trainer \
  --decision approved --comments "Actual review findings"

PYTHONPATH=. .venv/bin/python scripts/exercise_catalog.py media-review ASSET_ID \
  --hash APPROVAL_HASH --reviewer ACTUAL_PUBLISHER --role publisher \
  --decision approved --comments "Actual publication decision"

PYTHONPATH=. .venv/bin/python scripts/upload_local_footage.py publish \
  --asset-id ASSET_ID --hash APPROVAL_HASH --operator ACTUAL_OPERATOR \
  --release-id RELEASE_ID --dry-run
```

The reviewers must be different people. Use `--decision rejected` with the actual reason when rejecting an asset. Re-reviewing technique requires publication review again. Review events remain in the audit history.

After a successful dry run, remove `--dry-run` to publish. Preflight locks the asset and exercise and checks the exact approval hash, current review decisions, rights, technical validation, and published exercise status before copying bytes. It then copies only the derivative and poster into the public bucket and updates the read model transactionally. A failed copy leaves the previous visible pointer unchanged; successfully copied orphan derivatives are safe to reuse on retry because their bytes are immutable and already approved.

To restore previously published media, use the same command with its asset/hash and `--rollback`. The asset must still be approved. Rollback records a publication audit entry and changes pointers without deleting assets or exercise history.

The legacy direct-to-exercise URL assignment path has been retired. Uploaded standalone posters are ignored: posters must come from the validated video preparation step. Unknown IDs and private custom exercise IDs are never staged through this canonical-content tool.

## Verification performed

The automated media tests exercise dimensions, phone rotation, missing dependencies, duration limits, decoder failures, private staging, unchanged reruns, new footage keys, changed rights, rejected publication, failed storage copies, and approved derivative-only promotion.

A real neutral purple synthetic clip was encoded locally and processed twice: both 720×1280 H.264/yuv420p outputs and posters had matching hashes. This proves the technical transformation path; it is not exercise footage and provides no evidence of movement accuracy. Live R2 delivery, production CORS and actual exercise reviews remain pending credentials/domain/assets.
