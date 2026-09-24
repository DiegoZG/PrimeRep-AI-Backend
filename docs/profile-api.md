# Profile and private photos

All routes below require the current user's Bearer token. No route accepts another user's ID. Deploy Alembic revision `t5u6v7w8x9y0` before the Part 8 frontend and install the Pillow requirement.

## Profile

`GET /v1/users/me/profile` returns `id`, `preferred_name`, nullable `last_name`, read-only `email`, nullable `weight`, `weight_unit`, nullable `age` and `gender`, nullable `avatar_version`, and `updated_at`. The optional `weight_unit=LB|KG` query converts only the response; omission returns the stored unit (LB for legacy missing units). Responses are private/no-store.

`PATCH /v1/users/me/profile` accepts only supplied editable snake-case fields. Preferred name must be nonempty after trimming and at most 50 characters; last name is nullable and at most 50 characters. Age is an integer from 13 through 120. Gender uses Male, Female, Other, and Prefer not to say (case-insensitive input, canonical display labels). Optional body fields and last name can be cleared with null. Email, account ID, and unknown fields are rejected.

A non-null weight requires `weight_unit`; the physical maximum is 700 lb / 317.514659 kg, independent of unit. Changing only `weight_unit` converts the current weight. Clearing weight retains the selected unit. Historical set weights and existing plans are never rewritten by profile editing.

User columns remain authoritative for names/email. Current body values remain in onboarding JSON. Every explicitly edited body field is recorded in internal `onboarding_profiles.profile_managed_fields`; weight and weightUnit are protected together. Subsequent legacy full-onboarding payloads cannot restore these fields, including deliberately cleared values. Fields never edited through Profile remain writable during onboarding. Canonical User names/email are projected into onboarding reads and writes. A common User row lock serializes these updates.

## Photo

`PUT /v1/users/me/avatar` accepts a raw JPEG or PNG body with its matching `Content-Type`, up to 5 MiB. It returns the canonical profile with a new opaque avatar version. This is a raw binary upload, not multipart or base64 JSON.

Decoded images are verified, limited to 20 million pixels, rotated from EXIF orientation, stripped of metadata, composited on white when transparent, and normalized to a JPEG at most 512 pixels on its longest edge and 256 KiB. Unsupported type returns 415, oversized upload 413, and invalid decoded content 422. A failed replacement preserves the previous image/version.

`GET /v1/users/me/avatar` returns authenticated JPEG bytes with `Cache-Control: private, no-store` and `X-Content-Type-Options: nosniff`, or 404 when absent. `DELETE /v1/users/me/avatar` is idempotent and returns the canonical profile with a null avatar version. All mutation responses are private/no-store.

Thumbnails live in `user_avatars`, separate from public exercise media; account deletion cascades their removal. Clients must scope temporary images to the account, discard delayed responses after account changes, and clear caches on logout/deletion. There is no camera-upload flow or public avatar URL in this phase.
