# Part 8 — Profile Improvements, Support, and Data Portability

## Summary

Create `feat/profile-support-export` from updated `main` in both repositories. Merge the backend first.

Deliver:

- Editable names, private profile photos, and current body statistics.
- Real workout milestones and consecutive-active-week achievements.
- In-app help with contact-email support.
- A downloadable personal-data archive containing JSON and workout CSV files.
- Reliable account isolation, loading, error, and accessibility behavior throughout.

Diego will provide the visual design before frontend layout implementation. Backend work, contracts, and tests can proceed independently.

Social login and verified email changes are deferred. Future identity providers may initialize a profile, but subsequent sign-ins must preserve user-edited names and photos.

## 1. Profile Editing and Private Photos

### Profile contract and ownership

Add authenticated `GET/PATCH /v1/users/me/profile` for:

- Preferred name and optional last name.
- Current weight, presented in the selected display unit.
- Age and the existing gender field.
- Read-only email and photo-version metadata.

Use snake-case fields, consistent with the existing user contracts. PATCH changes only supplied fields; optional body fields can be cleared explicitly.

Keep `User` authoritative for names and the existing onboarding record authoritative for current body statistics. Update both transactionally where compatibility fields overlap. Ensure onboarding responses and subsequent writes cannot accidentally restore stale names or body values.

- Trim names; require a nonempty preferred name of at most 50 characters.
- Preserve existing age and gender choices.
- Validate weight after unit conversion, using a consistent physical limit across LB and KG.
- Store updated weight with an explicit unit; preserve existing records without rewriting historical weights.
- Do not regenerate an existing workout plan when profile details change.

Return the canonical saved profile and refresh shared authentication/profile state immediately. Fence reads and writes by authenticated identity, including the existing `refreshUser` path.

### Photo behavior

Users can choose a photo from their library, preview it, replace it, or remove it. Cancellation leaves the existing photo unchanged. Removing a photo restores initials.

For this phase, store one normalized thumbnail per user in a separate PostgreSQL avatar table with cascade deletion. This keeps small private avatars independent of public exercise-media storage and avoids requiring a new storage deployment.

- Add authenticated `GET/PUT/DELETE /v1/users/me/avatar`.
- Accept JPEG/PNG uploads up to 5 MB; validate decoded content and dimensions.
- Normalize orientation, strip metadata, and store a JPEG no larger than 512 pixels on its longest edge and 256 KB.
- Reject malformed images and decompression bombs.
- Replace the avatar transactionally; failed uploads retain the previous image.
- Serve only through authenticated access with private, no-store responses.
- Keep client image caches account-scoped and clear them on logout or deletion.

Use Expo SDK-compatible [ImagePicker](https://docs.expo.dev/versions/v54.0.0/sdk/imagepicker/) and [ImageManipulator](https://docs.expo.dev/versions/v54.0.0/sdk/imagemanipulator/) for selection, preview, and client normalization. Server validation remains authoritative. Camera capture is deferred.

### Profile presentation

Apply Diego’s design while retaining these behaviors:

- Compact identity header with an Edit Profile action.
- Real stats, activity, and Workout History.
- Small achievements preview linking to the complete collection.
- Settings access; account-management actions remain easy to find.
- Persistent Save/Cancel behavior, unsaved-change confirmation, and inline save errors.
- Account email is visible but read-only.

Refreshing Profile must update names, photos, units, body statistics, and workout-derived values without requiring an app restart.

## 2. Real Achievements

Add `GET /v1/users/me/achievements`, returning stable achievement identifiers, descriptions, earned dates, progress, and the timezone used.

Derive achievements from canonical completed sessions rather than expiring Coach feed items.

### Workout milestones

Award at 1, 5, 10, 25, 50, and 100 completed workouts, then every 50 workouts thereafter.

- Count completed sessions using the same definition as existing stats.
- Exclude abandoned and in-progress sessions.
- Use the qualifying session’s completion timestamp as the earned date.
- Reuse shared milestone definitions with Coach so its milestone messages agree with Profile.

### Consistency

Award at 2, 4, 8, and 12 consecutive active weeks.

- An active week contains at least one completed workout.
- Weeks run Monday–Sunday.
- Assign sessions by actual completion time, not their originally scheduled date.
- Use the account’s existing Coach timezone; initialize it from the device when absent, with UTC as the backend fallback.
- Calculate earned badges from the best historical sequence.
- An unfinished current week does not break the current sequence until that week ends.
- Multiple sessions in one week contribute one active week.
- Rest days and a later missed week do not remove legitimately earned historical badges.

Compute results from session history rather than maintaining a separate award ledger in this phase. Corrections to completion evidence can therefore update results; ordinary set-weight or rep edits do not affect these badges.

Show earned achievements and progress toward the next thresholds. Avoid leaderboards, calorie claims, forced celebration modals, or new achievement push notifications.

## 3. Help and Support

Add a Help & Support screen reachable from Settings.

Provide source-controlled, accessible FAQs covering:

- Workout logging and corrections.
- Programs and equipment replacements.
- Coach recommendations and weight units.
- Password recovery.
- Profile editing, export, and account deletion.

Add “Contact support” and “Report a problem” actions that open an email draft addressed to `EXPO_PUBLIC_SUPPORT_EMAIL`.

- Prefill a meaningful subject.
- Let users opt into adding app version, platform, and OS version.
- Do not automatically include workout content, body statistics, email identity, logs, or credentials.
- Opening a draft must not display “message sent.”
- If no mail application opens, display a selectable support address and useful fallback instructions.
- Missing configuration must produce an honest unavailable state and fail production-readiness validation.

No ticket database, in-app conversations, support dashboard, or promised response time is included.

## 4. Personal Data Export

Add authenticated `GET /v1/users/me/export`, returning a ZIP attachment. Accept the selected display unit for the CSV projection and label it explicitly.

The export screen explains what is included, generates the archive on request, and offers native sharing/saving or a browser download.

### Archive contents

Include:

- A versioned `account.json` containing allowlisted personal data.
- `workouts.csv`: one row per completed session.
- `sets.csv`: one row per current, nonremoved set from completed sessions.
- The normalized profile photo, if present.
- A README describing fields, units, timestamps, export boundaries, and schema version.

JSON covers account/profile and legal acceptance, onboarding and training preferences, equipment and favorites, private exercises and programs, activation snapshots, stored workout plans and sessions, notes and feedback, saved exercise questions/answers, and retained user-visible Coach content and preferences.

Include removed-set records in JSON with their removal status; exclude them from CSV calculations. Include server-stored unfinished sessions in JSON. The archive represents data stored by the service at generation time; device-only pending workout changes are outside this export.

Exclude password hashes, tokens, push credentials, internal AI prompts, worker bookkeeping, other users’ records, and private catalog-review or production documents.

### Generation and delivery

- Use explicit serializers rather than dumping ORM objects.
- Read a consistent database snapshot and iterate large collections in batches.
- Generate the ZIP through bounded temporary storage; clean up after delivery, failure, or disconnect.
- Apply an account-scoped limit of three exports per hour.
- Return authenticated downloads with `Cache-Control: no-store` and a safe filename.
- Escape CSV values correctly and neutralize spreadsheet formulas in user-entered text.
- Preserve canonical kilograms in JSON; label converted CSV values and duration units.
- Include generation time and schema version.

Use Expo’s [FileSystem](https://docs.expo.dev/versions/v54.0.0/sdk/filesystem/) and [Sharing](https://docs.expo.dev/versions/v54.0.0/sdk/sharing/) APIs for native delivery. Web uses an authenticated download and a temporary object URL.

Keep temporary downloads account-scoped, remove them after sharing finishes, and clear leftovers on logout and startup. Discard delayed responses after an account change. Share-sheet dismissal is silent.

No emailed download links, public export URLs, background export jobs, or import/restore feature are included.

## 5. Implementation, Verification, and Rollout

### Implementation order

1. Confirm current branches and preserve any unrelated work; create the feature branches from updated main.
2. Add profile/avatar persistence, contracts, and tests.
3. Add shared achievement calculations and the export service.
4. Implement frontend screens against the agreed design, including Help & Support.
5. Run integrated verification, document configuration and limitations, and prepare backend-first PRs with complete Markdown descriptions.

Follow existing user-service, account-fencing, screen-state, account-deletion, workout-summary, and Coach timezone patterns. Add new binary upload/download helpers without breaking the existing JSON HTTP client.

### Backend verification

- Profile validation, partial updates, unit conversion, and onboarding compatibility.
- Photo add/replace/remove, malformed and oversized files, metadata stripping, ownership, and deletion.
- Achievement thresholds, retroactive history, rest weeks, week/year boundaries, timezone changes, and completion corrections.
- Export completeness, empty accounts, large histories, unit labeling, CSV formula protection, consistent snapshots, cleanup, and cross-account isolation.
- Ensure credentials and internal operational fields never appear in exports.
- Migration upgrade/downgrade/upgrade against a disposable database.
- Focused suites followed by full authentication, deletion, workout, program, and Coach regressions.

### Frontend verification

- Profile edits persist across navigation and login; failed saves preserve entered values.
- Photo cancellation, replacement, deletion, permission failure, and initials fallback.
- Rapid account switching during profile, photo, achievement, and export requests.
- Achievements match actual workout history.
- FAQ navigation and support behavior with and without a mail application.
- Native export saving/sharing, browser download, cancellation, and retry.
- Light/dark themes, large text, screen-reader labels, 44pt controls, safe areas, and keyboard handling.
- Existing workout logging, exercise modal, history, settings, logout, and deletion remain functional.

Run lint, type checking, UI-action audit, Expo web export, applicable behavioral tests, Maestro validation and available simulator flows, and `git diff --check` in both repositories. Record unchanged baseline failures separately; report manual device checks honestly.

### Dependencies and deferred work

- Diego supplies the UI design before layout implementation.
- A real support mailbox is required before production release.
- Backend deployment precedes frontend rollout; native dependency changes require an updated native build.
- Social login/account linking, verified email changes, body-weight history, camera capture, public profiles, support conversations, and data import remain future work.
- Part 7.5 content approvals and video production continue independently.
