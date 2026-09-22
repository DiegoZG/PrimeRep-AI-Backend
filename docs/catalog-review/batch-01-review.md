# PrimeRep — Trainer Review: Batch 01 (25 exercises)

**Reviewed by:** Claude (Opus 5), an AI assistant — see "About this reviewer" below
**Review date:** 2026-09-21
**Source packet:** `docs/catalog-review/batch-01.html` (65,365 bytes, 25 drafts, all `generation_eligible: False`)
**Media:** none included; no video/image review performed.

## About this reviewer — read before filing this as a credentialed sign-off

You asked me to write as a certified personal trainer with 20 years of experience and to sign with a name, qualifications and date. I'm not able to do that part. I'm an AI, I hold no NASM/ACE/NSCA credential, no insurance, and no license, and a fabricated CPT name on a review of injury-relevant content is exactly the kind of record that causes problems later — for your users, and for you if anyone ever audits how the catalog was approved.

What I can do, and have done, is a substantive technical review at the level you asked for. Treat this as a thorough editorial and technical pass that materially reduces what a human reviewer has to do — not as the credentialed approval your packet is gated on. Your own packet says *"Trainer must verify setup, equipment attachments, range, muscle classification and load interpretation"* and *"this packet cannot approve content."* That's the right design. Keep `generation_eligible` gated behind a named human CPT/CSCS sign-off, and have that person countersign this document rather than replacing it.

Everything below is specific enough to be actioned or overruled quickly by that person.

---

## Part 1 — Systemic findings (fix these before the per-exercise edits)

These affect all 25 drafts and will affect the other 175. Fixing them at the pipeline level is worth more than any individual correction in Part 2.

### S1. `cues` is a duplicate of `benefits` in all 25 drafts — pipeline bug

Every exercise has exactly one cue, and that cue string is character-identical to the `benefits` paragraph. Example, Bench Press:

- cue: *"Practise moving the load away from the chest while keeping a stable support position."*
- benefits: *"Practise moving the load away from the chest while keeping a stable support position."*

These are different content types and should never be the same string. A benefit is descriptive third-person prose ("Builds pressing strength through the chest, shoulders and triceps"). A cue is a short, imperative, in-the-moment instruction the user reads between reps: *"Shoulder blades back and down."* / *"Elbows 45°, not flared."* / *"Bar to mid-chest, no bounce."*

As shipped, the cue field is dead weight in the UI. **This is the single biggest content defect in the batch.** Every exercise needs 2–4 real cues written fresh.

### S2. Cues, benefits, beginner guidance and references are inherited from a movement *family*, not written per exercise

Bench Press, Incline Dumbbell Press and Push-Up all carry the identical cue/benefit string. So do Chest Fly Machine and Cable Crossover; Overhead Press and DB Shoulder Press; all three curls; and Pushdown, Skull Crushers **and Parallel Bar Dip**.

Family inheritance is fine as a *scaffold*, but it has produced at least one flatly wrong assignment: the dip inherits *"Trains elbow extension while the shoulder position is kept relatively steady"* — which describes a pushdown. A dip is a loaded compound vertical push in which the shoulder travels through a large, and for some people risky, range. Anyone who reads that line and trains accordingly has been told the wrong thing about the exercise.

Recommendation: keep family inheritance for a `family_summary` field if useful, but require per-exercise `cues`, `beginner_guidance` and `references` before `generation_eligible` can flip true. Make it a schema constraint, not a review convention.

### S3. Beginner guidance is generic and wrong for bodyweight movements

The template *"Choose a light resistance and a range you can lower without losing your setup"* is applied to **Push-Up**, where there is no resistance to choose. **Pull-Up** and **Dip** get *"Select a resistance or assistance that permits control at both ends"* — but neither lists any assistance equipment (`pull_up_bar` and `dip_bar` only), so the guidance instructs an action the exercise's own data model doesn't support.

For bodyweight movements the beginner guidance *is* the regression ladder, and it must be written out:

- Push-up: wall → countertop/bench incline → knees → full → feet elevated
- Pull-up: ring/inverted row → band-assisted → assisted-pulldown machine → slow negatives (5–8s) → full
- Dip: bench dip → band-assisted → assisted dip machine → negatives → full

Add an `assistance_supported: true` flag and a regression/progression pointer (`regressions: [...]`, `progressions: [...]`) to these three at minimum.

### S4. `difficulty` is not calibrated — two ratings are clearly inverted

| Exercise | Draft | Should be | Why |
|---|---|---|---|
| **Pull-Up** | beginner | **advanced** (or intermediate *with* assistance modeled) | Most untrained adults cannot perform one strict rep. Labeling it "beginner" will put it in front of users who literally cannot execute it. |
| **Parallel Bar Dip** | beginner | **intermediate** | Full bodyweight on two hands with a demanding shoulder position. Not a first-month movement. |
| **T-Bar Row** | beginner | **intermediate** | Unsupported hinge under heavy plate loading; lumbar risk scales fast. |
| **Barbell Curl** | intermediate | **beginner** | One of the simplest movements in the gym. |
| **Preacher Curl** | intermediate | **beginner** | Pad-constrained, single joint, low skill demand. |
| Front Squat | intermediate | intermediate (defensible; **advanced** also defensible) | Rack position gates it on wrist/shoulder/T-spine mobility. |

Suggest defining `difficulty` explicitly in the schema — I'd anchor it on **skill and injury consequence**, not on how hard the set feels — and applying it consistently across all 200. Without a written definition, batch 8 will not match batch 1.

### S5. `movement_pattern` collapses different joints under one label

`flexion` is used for **Front Raise** (shoulder flexion) and for **Barbell Curl / Dumbbell Curl / Preacher Curl** (elbow flexion). `extension` is used for **Tricep Pushdown / Skull Crushers** (elbow extension). A workout generator reading these as equivalent could substitute a front raise for a biceps curl. Split into `shoulder_flexion` / `elbow_flexion` / `elbow_extension`, or add a `joint` field.

Related: **Incline Dumbbell Press** is tagged `horizontal_push`. It is genuinely between horizontal and vertical. If the generator caps "one horizontal push per session," bench + incline will never be programmed together even though that's a standard pairing. Consider an `incline_push` value or a `secondary_pattern` field.

### S6. Steps describe equipment the record doesn't list — a logging correctness problem

- **Preacher Curl**: `equipment_ids: ['preacher_curl_bench', 'short_bar']`, `resistance_modality: barbell`, `load_profile.basis: total` — but step 1 says *"grip the bar (or dumbbell)"*.
- **Skull Crushers**: `equipment_ids: ['ez_bar', 'flat_bench']`, basis `total` — but step 1 says *"holding an EZ-bar or dumbbells"*.

If a user follows the dumbbell option, the load basis is wrong (`per_implement`/2, not `total`/1) and their logged numbers are silently meaningless. **Either drop the parenthetical from the steps, or split into separate records.** Splitting is cleaner and probably already necessary — see Part 3.

Same class of problem, different field:

- **Front Raise** and **Dumbbell Curl** are `laterality: bilateral`, but both say *"raise/curl one or both."* Alternating and simultaneous are different for set counting (is 10 alternating reps 10 total or 10 per arm?). Pick one in the steps, or add `laterality: alternating` and define rep counting for it.

### S7. Weight-tracking bases — mostly right, four gaps

The `load_profile` assignments are correct in 21 of 25. The exceptions:

1. **Pull-Up and Dip** (`basis: bodyweight`) have **no way to record assistance or added load.** These are the two exercises in the batch most often performed with a band, an assist machine, or a belt and plate. A user who goes from −40 lb assisted to +25 lb weighted over six months will show a flat line in PrimeRep. Add a signed `load_delta` field (negative = assistance, positive = added) and make `bodyweight` mean "bodyweight ± delta." Same field would let Push-Up record a weighted vest.
2. **T-Bar Row** (`basis: machine` on `plate_loaded_t_bar`). "Machine" implies a selectorized stack with printed numbers. This is plate-loaded, so the honest basis is `total` plate weight, and the record needs to state explicitly whether the apparatus/handle mass counts (recommend: plates only, stated in-app).
3. **Cable Crossover** (`basis: machine`, `implement_count: 1`) uses **two independent stacks.** Does 30 lb mean 30 per side or 60 total? Set `implement_count: 2` with `basis: per_implement`, or keep 1 and put "per side" in the UI label. Either is fine; ambiguity is not.
4. **Bench Press / Deadlift / Squat / Front Squat / OHP / Barbell Curl** (`basis: total`) are correct, but "total" must be defined in-app as *including the bar*. This is the #1 source of logging disputes in every training app I'd expect you to be benchmarked against. Put "incl. bar" in the input hint.

One further note: **Leg Press** numbers are not comparable across machines (sled angle, carriage counterweight, plate-loaded vs selectorized vary enormously). Not a defect, but consider suppressing leg press from any cross-user comparison or "strength score" feature.

### S8. The `benefits` text is vague to the point of being unhelpful to users

*"Trains coordinated knee and hip extension in a squat pattern"* is an anatomical restatement, not a benefit. Users want to know what it does for them: builds lower-body strength and size, carries over to stairs/carrying/sport, loads the whole system efficiently. The current phrasing reads like it was written to be defensible rather than useful. You can be accurate and still say something.

---

## Part 2 — Per-exercise review

Verdicts: **Approved** (ship-ready pending S1–S3 fixes) · **Needs changes** (specific corrections required) · **Outside my expertise**.

Note that S1 (cues) technically applies to all 25; "Approved" below means *nothing else is wrong with this record*.

---

### 1. Bench Press — **Needs changes**

Instructions are accurate. Eyes-under-bar, grip slightly wider than shoulders, elbows ~45°, blades pinned, glutes down — all correct. The three mistakes are the right three.

**Required additions:**
- **Safety is entirely absent.** No mention of a spotter, and no mention of setting safety pins/arms in a rack. This is the exercise people are killed by. For a solo lifter, the instruction must be: use safety arms set just below chest height, or don't use collars if benching in an open rack, or have a spotter. Non-negotiable before `generation_eligible: true`.
- Add a mistake: **thumbless / "suicide" grip.** Wrap the thumbs.
- "Press back up in a slight arc until arms are fully extended" — add *"without locking out hard or losing the blade position at the top."*

**Classification:** correct. `intermediate` is right (note: the cited ACE page rates barbell chest press *beginner* — see Part 4).
**Weight:** `total`, incl. bar. Correct.
**Beginner guidance:** should name the substitutions — dumbbell press or machine chest press until a competent spotter/safeties are available.
**Generation:** eligible **only** once safety text is added, and I'd gate it on the user having `flat_bench` + a rack with safeties, not just a bench.

---

### 2. Incline Dumbbell Press — **Needs changes** (minor)

Setup and execution are good; 30–45° is the right prescription and the "too steep becomes a shoulder press" mistake is well stated.

**Required additions:**
- **Getting out of the set.** The kick-up is described; the dismount isn't. Heavy dumbbells at the bottom of an incline are how people tear pecs and drop weights on themselves. Add: *"To finish, bring the dumbbells to your chest, tuck your chin, and sit up with them — or set them down one at a time. Never let them fall backward with straight arms."*
- Add a wrist cue: stack the wrist over the elbow, don't let the bells tip back.

**Classification:** correct, but see S5 on `horizontal_push`.
**Weight:** `per_implement` × 2 — correct. UI must say "per dumbbell."
**Generation:** eligible.

---

### 3. Push-Up — **Needs changes**

Instructions are accurate and the mistakes are the right ones.

**Required changes:**
- **Beginner guidance is wrong for this exercise** (S3). Replace with the regression ladder: wall → bench/counter incline → knees → full. This is the single most-needed fix in the record.
- Add hand placement precision: hands under or *slightly below* the shoulder line, not up by the neck. Getting this wrong is the most common reason push-ups hurt people's shoulders.
- Add to mistakes: **leading with the head/chin** instead of the chest.

**Classification:** correct, `abs` as secondary is right.
**Weight:** `bodyweight` — correct, but add the `load_delta` field (S7) so a weighted vest is loggable.
**Generation:** eligible, and it's one of the few here needing no equipment gating.

---

### 4. Chest Fly Machine — **Needs changes**

Only two steps, and the most important setup variable is missing.

**Required additions:**
- **Seat height.** Handles should sit at roughly mid-chest / armpit height with the upper arms near shoulder level. Wrong seat height is the difference between a pec fly and a shoulder-capsule stretch. This must be step 1.
- Specify the elbow: a *fixed* slight bend held throughout — the current "slight bend in your elbows" doesn't say it stays fixed, which is what separates a fly from a press (and the first listed mistake depends on it).

**Classification:** correct. `adduction` is a reasonable label for horizontal adduction. `beginner` is right.
**Beginner guidance:** *"Start with a short, comfortable arc and light resistance; a deeper stretch is not a requirement"* — this is the best-written beginner line in the batch. Keep it verbatim and consider reusing the pattern elsewhere.
**Weight:** `machine` — correct.
**Generation:** eligible after the seat-height step is added.

---

### 5. Cable Crossover — **Needs changes**

Setup and execution are good. Staggered/split stance and forward lean are correctly described.

**Required correction:**
- Mistake 3 — *"Setting the pulleys too low (rather than high…) changes the muscle emphasis away from the chest"* — **is not a mistake and should be removed or reworded.** Low-to-high crossovers are a standard, legitimate variation emphasizing the upper/clavicular pec. As written you're telling users a valid exercise is an error. Reword to: *"Pulley height changes emphasis — high-to-low favors the lower chest, low-to-high the upper chest. Pick one and stay consistent within a set."*

**Classification:** correct.
**Weight:** ambiguous — see S7 item 3. Must be resolved before this can be logged meaningfully.
**Generation:** eligible after the weight basis is disambiguated.

---

### 6. Overhead Press — **Needs changes** (minor)

Technically the strongest draft in the batch. "Head back, then through once the bar clears the face" is the correct bar-path coaching and is often omitted.

**Required additions:**
- **Wrist position:** the wrist must be stacked under the bar, not bent back. Very common source of wrist pain here.
- Add a precaution: this is a mobility-gated lift. Users who can't reach a bar-over-ears lockout without lumbar extension should use a **landmine press or incline press** instead of forcing the range. The current guidance (*"demonstrate the range without weight first"*) is good but stops short of naming the alternative.
- Heavy work should be done in a rack with pins, not cleaned from the floor by a novice.

**Classification:** `secondary_muscles: ['triceps', 'back']` — "back" is imprecise. For a *standing* press the honest secondaries are triceps, upper traps/serratus, and **abs/core** (the trunk is doing real work). Add `abs`.
**Weight:** `total`, incl. bar. Correct.
**Generation:** eligible.

---

### 7. Dumbbell Shoulder Press — **Approved** (with two additions)

Clean, accurate, appropriately scoped. Mistakes are the right ones.

**Suggested additions:**
- Getting the bells to the start: kick them up from the thighs one at a time; don't curl them up.
- Note that neutral (palms-facing) grip is a valid and often more shoulder-friendly option — worth one line, or a separate record if you want to track them apart.

**Classification:** correct. `beginner` is right.
**Weight:** `per_implement` × 2 — correct.
**Generation:** eligible.

---

### 8. Dumbbell Lateral Raise — **Needs changes** (minor)

"Lead with the elbows" is the correct cue and the momentum warning is the right #1 mistake.

**Required changes:**
- `secondary_muscles: []` is wrong — the **upper traps** are meaningfully involved, and the draft's own mistake #2 says so. Add `traps` (or `shoulders`+`traps` per your taxonomy).
- Mistake 3, *"Locking the elbows completely straight can place unnecessary stress on the elbow joint"* — overstated. The real reason for the soft elbow is leverage, not joint safety. Reword or drop.
- Mistake 2, *"Raising the arms too high above shoulder height also recruits the traps more than intended"* — defensible as practical coaching, but it's a preference, not an error. Suggest: *"Above shoulder height the traps take over; stop at shoulder level if the side delt is the target."*

**Classification:** otherwise correct. `beginner` right.
**Weight:** `per_implement` × 2 — correct.
**Generation:** eligible.

---

### 9. Dumbbell Front Raise — **Needs changes**

Instructions are fine. The third mistake — that the front delts are already heavily worked by pressing, so overdoing this adds unnecessary fatigue — is genuinely good, useful coaching and rare to see written down. Keep it.

**Required change:**
- *"raise one or both dumbbells"* conflicts with `laterality: bilateral` (S6). Resolve.

**Classification:** `movement_pattern: flexion` collides with the curls (S5).
**Weight:** `per_implement` × 2 — correct.
**Generation:** eligible, but low priority for auto-inclusion — by the draft's own reasoning this is an accessory most users don't need programmed automatically. Consider `generation_eligible: true` with low selection weight, or reference-only.

---

### 10. Reverse Fly — **Needs changes**

Execution described correctly.

**Required addition:**
- **The chest-supported variation must be mentioned.** A standing bent-over hinge held for 12+ reps loads the lumbar spine continuously, for an exercise whose target muscle is small and needs almost no load. Lying face-down on a 30–45° incline bench removes that entirely and is the better default for most users and nearly all beginners. As written, the record recommends the harder-on-the-back version by omission.
- The beginner guidance (*"practise the arm path without weight"*) is good — extend it to name the incline-bench option.

**Classification:** `horizontal_pull` with `primary: shoulders` is reasonable. `secondary: ['back']` would be more useful as rhomboids/mid-traps if your taxonomy supports it.
**Weight:** `per_implement` × 2 — correct.
**Generation:** eligible after the variation note is added.

---

### 11. Deadlift — **Needs changes** (highest-priority record in the batch)

**Classification error:** `primary_muscle: back` is wrong. In a conventional deadlift the prime movers are the **hip extensors — glutes and hamstrings** — with the quads contributing off the floor. The back (erectors, lats) works hard but **isometrically, as a stabilizer**; it is not the muscle producing the movement. Set `primary_muscle: glutes` (or `hamstrings`, or a `posterior_chain` value if you have one), and secondaries to hamstrings/glutes + `back` + `quads` + `forearms`. As it stands, a generator building a "back day" will program heavy deadlifts as a back exercise and a "leg day" will skip them — both wrong.

**Required instruction fixes:**
- *"drive through your heels"* → **mid-foot.** Heel-only cueing tips people backward and is a known cause of the bar drifting. Whole-foot pressure with the weight slightly toward the heel.
- Add setup precision: bar over mid-foot, shins roughly an inch from the bar before you reach down.
- **Grip is not mentioned at all.** Double-overhand until it fails, then hook or mixed — and state that a mixed grip carries a biceps-tear risk on the supinated side, so alternate it.
- **Bracing is not mentioned.** Big breath into the belly, brace, hold through the rep.
- Add: reset between reps for beginners rather than touch-and-go.

**Weight:** `total`, incl. bar — correct.
**Difficulty:** `intermediate` is right; I'd accept `advanced`.
**Generation:** This is the record I'd hold back longest. Heavy barbell deadlifts auto-programmed to a self-coaching novice is the highest-consequence thing in this batch. Options: keep `generation_eligible: false` and reference-only; or make eligibility conditional on the user self-reporting prior barbell experience; or auto-program only a capped rep range with an explicit "form check" prompt. Your call, but make it deliberately.

---

### 12. Romanian Deadlift — **Approved** (with two refinements)

Genuinely well written. *"The range of motion should be limited by hamstring flexibility, not by rounding the spine"* is the correct and important distinction, and most published descriptions get it wrong. Mid-shin as a typical endpoint is right.

**Refinements:**
- *"legs almost straight with just a slight knee bend"* → better: **set ~15–20° of knee bend at the start and hold it constant.** "Almost straight" invites people to lock out and turn it into a stiff-leg deadlift.
- The RDL starts from standing, which means the bar has to get to hip height first. For a beginner that first rep is itself a deadlift. Add: take it from a rack, or deadlift it up once and then begin.

**Classification:** `primary: hamstrings`, `secondary: glutes, back` — correct, and a good contrast with the error in #11.
**Weight:** `total`, incl. bar — correct.
**Generation:** eligible.

---

### 13. Pull-Up — **Needs changes**

Instructions are accurate.

**Required changes:**
- **`difficulty: beginner` is wrong** (S4). Set to `advanced`, or `intermediate` only once assistance is modeled.
- **Beginner guidance is unactionable** (S3) — it refers to assistance the record doesn't support. Write the real ladder: inverted rows → band-assisted → assisted machine → 5–8 second negatives → full reps.
- Add `forearms`/grip to secondaries.
- Consider softening mistake #1: kipping is a legitimate technique in CrossFit contexts. *"for most training goals"* already hedges it, which is fair — just make sure the phrasing doesn't read as a blanket safety claim, because that isn't what it is.

**Weight:** `bodyweight` with no delta — see S7. Blocking issue for a movement this commonly assisted or weighted.
**Generation:** **Not eligible as written.** A generator that inserts pull-ups for a user who can't do one produces a failed workout on day one. Make eligibility conditional on either assistance being supported or the user confirming they can perform ≥3 reps.

---

### 14. Lat Pulldown — **Approved**

Accurate throughout. The behind-the-neck warning is correct and worth keeping prominently — it's a real and still-common injury pattern.

**Optional additions:** set the thigh pad snug *before* taking the bar; stand to reach the bar rather than pulling yourself down into the seat with it.
**Classification / weight:** correct.
**Generation:** eligible. Good default substitute when Pull-Up is gated out.

---

### 15. Seated Cable Row — **Needs changes** (minor)

Execution is right, including the elbows-back and blade-retraction cues.

**Required additions:**
- **Entry and exit.** Reaching forward to grab the handle with straight legs and a rounded back, or letting the stack yank you forward at the end of the set, is where people hurt themselves on this machine — not during the reps. Add: bend the knees to reach the handle, and return it to the rack with bent knees.
- Add the neutral-spine setup to step 1 (chest up, ribs down) — mistake #1 references it but the steps never establish it.

**Classification / weight:** correct.
**Generation:** eligible.

---

### 16. T-Bar Row — **Needs changes**

Execution is described correctly and the back-rounding warning is properly prioritized.

**Required changes:**
- **`difficulty: beginner` → `intermediate`** (S4).
- **Load basis** `machine` is misleading on a plate-loaded landmine (S7 item 2). Use `total` plate weight and state in-app whether the apparatus counts.
- Mention the **chest-supported T-bar** variant as the lower-back-friendly option, same reasoning as #10.
- Add a precaution: not a default choice for users reporting low-back issues.

**Classification:** correct; consider adding rear delts and forearms to secondaries.
**Generation:** eligible after difficulty and basis are fixed; I'd exclude it from auto-selection for users who flag back problems.

---

### 17. Barbell Curl — **Needs changes** (minor)

Accurate. "Cheat curling" is correctly identified as the main error.

**Required changes:**
- **`difficulty: intermediate` → `beginner`** (S4).
- Add the **EZ-bar option**. A straight bar forces full supination and is a common source of wrist and elbow discomfort; the EZ-bar solves it. One line, or a separate record.

**Classification / weight:** correct (`total`, incl. bar).
**Generation:** eligible.

---

### 18. Dumbbell Curl — **Needs changes**

**Required changes:**
- *"palms facing forward (or neutral, depending on preference)"* — **a neutral grip throughout is a hammer curl**, a different exercise emphasizing the brachioradialis and brachialis. This almost certainly duplicates a separate hammer curl record elsewhere in the 200 (see Part 3). Remove the parenthetical, or split the records.
- *"Curl one or both"* conflicts with `laterality: bilateral` (S6). Resolve, and define rep counting if you add an alternating mode.

**Classification / weight:** otherwise correct.
**Generation:** eligible once the grip ambiguity is resolved.

---

### 19. Preacher Curl — **Needs changes** (minor)

The first mistake — that fully locking out at the bottom under load stresses the elbow and biceps tendon, and a slight bend is safer — is **the most valuable single warning in the batch.** The preacher bottom position is where distal biceps tendons get injured, and most exercise libraries don't say so. Keep it exactly as written.

**Required changes:**
- **`difficulty: intermediate` → `beginner`** (S4).
- Resolve the bar/dumbbell equipment mismatch (S6).
- Add: set the seat so the armpits rest against the top of the pad; a too-low seat forces shoulder elevation.

**Classification / weight:** correct given a bar.
**Generation:** eligible.

---

### 20. Tricep Pushdown — **Approved** (with one note)

Accurate and appropriately scoped.

**Note:** rope and straight-bar pushdowns behave differently (neutral vs pronated wrist, and the rope allows end-range separation). Both currently log under one ID, so a user swapping attachments will see an unexplained weight change. Either note it in the description or split the records.
**Classification / weight:** correct. `beginner` right.
**Generation:** eligible.

---

### 21. Skull Crushers — **Needs changes**

Execution is right, and *"toward your forehead or just behind your head"* correctly captures both paths.

**Required additions:**
- **Elbow-pain precaution.** This is one of the most common elbow-irritating movements in general training. Add: if the elbows hurt, lower behind the head rather than to the forehead, switch to an EZ-bar, or substitute a pushdown/overhead cable extension.
- **Solo-lifter safety.** A barbell descending toward your face with no spotter means: don't train this to failure, and bail to the side or behind the head — never let it come down on the face. The draft mentions "risk of hitting the head" as a mistake but gives no instruction for what to do about it.
- Resolve the EZ-bar/dumbbell mismatch (S6).

**Classification:** correct. `intermediate` is right.
**Weight:** `total` — must state whether the EZ-bar's own weight (usually ~15–25 lb, and it varies) is included. Recommend: yes, include, and prompt the user for their bar's weight once.
**Generation:** eligible after the safety and mismatch fixes.

---

### 22. Parallel Bar Dip — **Needs changes**

Execution is described well and *"lower until the upper arms are roughly parallel"* is an appropriately conservative depth recommendation.

**Required changes:**
- **The inherited cue is wrong for this exercise** (S2). *"Trains elbow extension while the shoulder position is kept relatively steady"* describes a pushdown. In a dip the shoulder moves into significant extension under full bodyweight. Rewrite.
- **`difficulty: beginner` → `intermediate`** (S4).
- **Steps and classification contradict each other.** *"Leaning your torso slightly forward"* is the **chest-emphasis** dip; `primary_muscle: triceps` describes the **upright** dip. Pick one: upright torso + elbows tucked → triceps primary; forward lean + slight elbow flare → chest primary. Ideally, split into two records.
- **Add a shoulder precaution.** Dips are contraindicated or need modification for users with anterior shoulder pain, impingement history, or prior shoulder instability. The draft's first mistake gestures at this ("descending too deep… can place excessive strain on the front of the shoulder") but doesn't tell anyone to avoid or modify the exercise.
- Beginner guidance is unactionable (S3) — give the ladder: bench dips → band-assisted → assist machine → negatives.

**Weight:** `bodyweight` with no delta — same blocking issue as Pull-Up (S7 item 1).
**Generation:** **Not eligible as written.** Same reasoning as Pull-Up: gate on assistance support or a self-reported rep capability.

---

### 23. Back Squat — **Needs changes**

Instructions are accurate and the three mistakes are well chosen — valgus collapse correctly gets top billing.

**Required additions:**
- **Rack safety is entirely absent**, exactly as with Bench Press. Set the safety pins/arms just below your bottom position; know how to bail (sit down, let the bar roll off the back onto the pins). Unracking and walking out is its own skill and deserves a line. **Blocking for auto-generation.**
- Bar placement: *"upper back, not your neck"* is right — specify **on the rear delts / mid-traps shelf**, and that the shelf is created by pulling the elbows down and squeezing the blades.
- Depth: *"at least parallel"* should be softened to **"to the depth at which you can hold a neutral spine and stable foot pressure — for many people that's parallel or below, but it's individual."** Prescribing parallel as a floor for everyone is how people get pushed into the lumbar rounding the draft's own mistake #2 warns about.

**Classification:** `quads` primary with glutes/hamstrings secondary — correct. Consider adding adductors and erectors/abs.
**Weight:** `total`, incl. bar — correct.
**Generation:** eligible **only** after rack-safety text is added, and I'd gate on `squat_rack` with safeties present (which `equipment_ids` already requires — good).

---

### 24. Front Squat — **Needs changes** (minor)

The elbow-drop mistake is correctly identified as the #1 breakdown, and the wrist-mobility mistake is the right #2.

**Required additions:**
- **Name the rack positions explicitly:** clean grip (fingertips under the bar, elbows high), cross-arm grip, or straps. Currently only implied by *"hands lightly supporting the bar (or crossed over the top)."*
- State clearly that **the bar rests on the front delts, not on the hands and not on the throat.** The hands only stop it rolling forward.
- Add bail instructions: a missed front squat is dumped forward off the shoulders. Safeties still apply.

**Classification:** correct. `intermediate` defensible; `advanced` also defensible given the mobility gate.
**Weight:** `total`, incl. bar — correct.
**Generation:** eligible after the additions; consider gating on the user having done back squats, since the rack position is a genuine barrier.

---

### 25. Leg Press — **Needs changes** (minor)

Execution is right, the depth caution is right, and not locking out the knees is correct.

**Required additions:**
- **"Never place your hands on your knees."** Standard and important safety instruction for this machine, and it's missing.
- Reinforce using the **safety catches** — step 2 says "release the safety catches" but never says to re-engage them before getting out from under the sled.
- Terminology: *"butt wink"* conventionally describes posterior pelvic tilt at the bottom of a **squat**. On the leg press the phenomenon is the pelvis rolling off the pad. Comprehensible, but loose — suggest describing it plainly instead.

**Classification:** `quads` primary, `glutes` secondary — correct; hamstrings contribute minimally and could be added.
**Weight:** `machine` — correct, but see the cross-machine comparability note in S7.
**Generation:** eligible.

---

## Part 3 — Duplicates and overlap

**No exact duplicates within Batch 01.** Five overlap risks to check against the remaining 175:

1. **Dumbbell Curl currently absorbs the Hammer Curl** via the "(or neutral)" grip option (#18). If a `hammer_curl` record exists elsewhere, these collide. Highest-confidence overlap in the batch.
2. **Skull Crushers permits dumbbells** (#21), overlapping a likely `lying_dumbbell_triceps_extension` record.
3. **Preacher Curl permits a dumbbell** (#19), overlapping a likely `dumbbell_preacher_curl`.
4. **Parallel Bar Dip** (#22) — if the steps stay forward-leaning while the classification stays triceps-primary, this will contradict any separate `chest_dip` record. Resolve by splitting.
5. **Chest Fly Machine and Cable Crossover** (#4, #5) share identical cues, benefits and beginner guidance and both are `adduction`/`chest`. These are legitimately different exercises and should both stay — but the generator should treat them as *substitutes*, not as two independent slots in the same session. Worth an explicit `substitutes_for` relation.

Also flag for the schema: **Bench Press has aliases `['bench', 'barbell bench press']` while Incline Dumbbell Press has `aliases: []`.** Alias coverage is inconsistent across the batch (10 of 25 have none). If aliases drive search, users typing "incline press," "RDL" (present — good), "pec deck," "cable fly," "lat pull," "OHP" (present — good), "military press," "shoulder press," "leg extension" etc. will get inconsistent results. Worth a systematic pass.

---

## Part 4 — Sourcing

Every reference in this batch points to the ACE Exercise Library, one citation per exercise, inherited at the family level. I checked the URLs; they resolve.

**Finding 1 — two different pages share the display name "Chest Press."** `/exercise-library/5/chest-press/` is the **barbell** chest press (ACE rates it beginner). `/exercise-library/19/chest-press/` is the **dumbbell** chest press (ACE rates it intermediate). Both appear in the packet under the identical label "ACE Exercise Library — Chest Press." A reader cannot tell which supports which claim. Include the ID or the variant in the display name.

**Finding 2 — several citations cannot support the exercise they're attached to.** A press page does not establish anything about a fly:

| Exercise | Cited page | Problem |
|---|---|---|
| Push-Up | ACE Chest Press (barbell) | Different exercise, different modality |
| Chest Fly Machine | ACE Chest Press (dumbbell) | Press cited for a fly |
| Cable Crossover | ACE Chest Press (dumbbell) | Press cited for a fly |
| Reverse Fly | ACE Bent-over Row | Row cited for a rear-delt fly |
| T-Bar Row | ACE Bent-over Row | Closer, but not the same setup |
| Pull-Up | ACE Seated Lat Pulldown | Different exercise (and different difficulty class) |
| Romanian Deadlift | ACE Deadlift | Conventional cited for the RDL |
| Parallel Bar Dip | ACE Triceps Extension | Isolation page cited for a compound |
| Front Squat / Leg Press | ACE Bodyweight Squat | Unloaded bodyweight cited for loaded variants |
| Preacher Curl | ACE Bicep Curl | Standing curl cited for pad-supported |

To the packet's credit, several records *disclose* this (*"Reference establishes chest-training anatomy; the fly variation requires separate trainer verification"*). That honesty is good practice and I'd keep it. But it means **10 of 25 exercises currently have no source supporting their specific content** — which is a different thing from having no source at all, and worth tracking as its own field (`reference_covers: family | exercise`).

**Finding 3 — the supporting sentences are near-tautological.** *"Chest pressing involves shoulder movement and elbow extension."* *"Curls train elbow flexion while the torso remains stable."* These are true but do no work; they'd support almost any claim, which means they support none of the specific ones (elbow angles, depth recommendations, difficulty ratings, muscle attributions). If sources are meant to be load-bearing, they need to be specific enough to check.

**Finding 4 — the cited source disagrees with your difficulty on at least one record.** ACE rates the barbell chest press *beginner*; your Bench Press draft says *intermediate*. I think intermediate is the better call, but if you cite a source it should be noted where you deviate from it.

**Note on the accessed dates:** all references show `accessed 2026-09-20`, which matches the file mtimes. Consistent and plausible.

---

## Part 5 — Generation eligibility summary

All 25 are currently `generation_eligible: False`. Recommended state after the corrections above are applied and a human trainer signs off:

**Eligible without conditions (15):** Incline DB Press, Push-Up, Chest Fly Machine, Cable Crossover, Overhead Press, DB Shoulder Press, Lateral Raise, Reverse Fly, Romanian Deadlift, Lat Pulldown, Seated Cable Row, Barbell Curl, DB Curl, Preacher Curl, Tricep Pushdown

**Eligible with a gate (7):**
- **Bench Press**, **Back Squat**, **Front Squat** — require rack-with-safeties in the user's equipment profile, and require the added safety text
- **T-Bar Row** — exclude for users reporting low-back issues
- **Skull Crushers** — after the elbow/solo-lifter precautions
- **Leg Press** — fine to program; exclude from any cross-user strength comparison
- **Front Raise** — eligible but low selection weight; it's an accessory most programs don't need

**Hold as reference-only for now (3):**
- **Pull-Up** and **Parallel Bar Dip** — until assistance is modeled (S7) and difficulty is corrected. A generator that serves these to a beginner produces a workout they cannot perform.
- **Deadlift** — a deliberate product decision, not a content one. The record can be made correct; whether a self-coaching novice should be auto-assigned heavy barbell deadlifts without a form check is a question for you and your trainer, and I'd want it answered explicitly rather than by default.

---

## Part 6 — What this review does not cover

- **Video, images, and demonstrated technique.** No media was included, so nothing here evaluates whether a demonstration matches the text.
- **Medical contraindications and clinical populations.** I've flagged where a precaution is missing (shoulder issues on dips, low-back on T-bar row and reverse fly), but screening criteria, injury rehabilitation, pregnancy modifications, and clearance thresholds are **outside my expertise** and outside what any exercise library should assert. If PrimeRep intends to auto-program to users who self-report injuries, that logic needs a qualified clinician's input, not a trainer's and not mine.
- **Programming variables.** Sets, reps, loads, rest, and progression schemes aren't in these records and weren't reviewed.
- **Legal/licensing.** The packet notes media licensing is pending. I've only assessed whether cited sources support the claims made, not whether any usage rights exist.
- **Batches 02–08.** This covers Batch 01 only, as requested.

---

*Prepared by Claude (Opus 5), AI assistant, 2026-09-21. Not a credentialed fitness professional; see "About this reviewer." This document is intended as preparatory technical review to be countersigned by a qualified human trainer before any `generation_eligible` flag is set.*
