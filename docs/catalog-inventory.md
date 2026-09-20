# Catalog inventory — 2026-09-20

Reproduce the static inventory with `PYTHONPATH=. .venv/bin/python scripts/catalog_inventory.py`; add `--database` for read-only aggregate inspection using the configured environment.

| Check | Result |
|---|---:|
| Canonical migration exercises | 41 |
| Existing source-controlled content records | 41 |
| Missing canonical content records | 0 |
| Content records with no canonical identity | 0 |
| New individually authored drafts | 159 |
| Combined draft target | 200 |
| Batches / maximum size | 8 / 25 |
| Normalized name/alias collisions | 0 |
| Invalid draft equipment references | 0 |
| Frozen equipment seed records | 97 |
| Original seed image/video URLs | 0 |
| Human trainer/publication approvals supplied | 0 |
| Original exercise media assets supplied | 0 |

Older workspace documentation mentioned 43 exercises and 130 equipment entries; those counts do not match the frozen canonical migrations inspected here. Existing IDs remain unchanged.

The local development database was inspected in a read-only transaction on 2026-09-20. It contained all 41 canonical seed exercises, no extra seed exercise rows, no missing `how_to` text on those seeds, no seed image/video URLs and no broken equipment links. There were 354 user-source exercise rows, excluded from this target; two had no owner and were active. Their names, owners and content were not reported, and no database rows were changed by the inventory. Application visibility must continue excluding orphaned custom exercises.

The 200-entry draft distribution covers 52 dumbbell, 39 bodyweight, 30 cable, 26 barbell, 41 machine, 8 band, 3 kettlebell and 1 other resistance entry. Fifteen movement-pattern classifications are represented. These are draft classifications awaiting review, not verified coverage claims.

Three anchored band entries have an unresolved rated-anchor equipment requirement. All drafts keep generation disabled until eligibility is explicitly reviewed. The report lists identity-review candidates for grip, stance and load-position variations. Plate-versus-stack versions of otherwise identical chest press, hack squat and leg press were removed from the target to avoid counting machine models as movements.

Research provenance is attached to every draft, with narrow source claims and explicit variant uncertainty. The review packets distinguish these editorial drafts from approved content. The three-exercise production comparison and subsequent 25-exercise media expansion remain pending actual candidate assets, permissions and reviews.
