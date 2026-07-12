# Epics

## Epic 1: Failure Retry Loop

**Objective:** When a turn ends in the terminal "I couldn't fully complete this" floor response, the failure retries itself automatically in the background (capped at 3 attempts, forced onto a different capability each time), and a user's "do it again" triggers the same failure-aware retry immediately instead of a blind re-ask.

**Business value:** Removes the manual retry burden from the user and stops the assistant from repeating an already-failed approach.

**Source documents (full detail — read these, this epic file is a stub):**
- Spec: `docs/superpowers/specs/2026-07-12-failure-retry-loop-design.md`
- Implementation plan (8 tasks, complete code, TDD steps): `docs/superpowers/plans/2026-07-12-failure-retry-loop.md`

**Dependencies:** None — first epic. (Epic 2 "like/dislike feedback buttons" and Epic 3 "token usage display" are planned next, not yet specced.)

---

### Story 1.1: Retry Queue Migration

As the platform, I want a `retry_queue` table, so that floored turns have somewhere durable to record their retry state.

**Acceptance criteria:**
- `retry_queue` table exists with columns per the spec's Data Model section.
- Migration is idempotent (`CREATE TABLE IF NOT EXISTS`).

**Source hint:** Plan Task 1 (`docs/superpowers/plans/2026-07-12-failure-retry-loop.md`).

### Story 1.2: RetryQueueStore

As the pipeline, I want a store wrapping `retry_queue` CRUD, so that other components don't write raw SQL.

**Acceptance criteria:**
- `insert_pending`, `backfill_channel_message`, `get_due`, `get_latest_pending_for_session`, `mark_completed`, `mark_attempt_failed` all implemented and tested.
- `mark_attempt_failed` caps status at `failed` after 3 attempts.

**Source hint:** Plan Task 2.

### Story 1.3: Insert Pending Row On Floored Turns

As the platform, I want every floored turn to create a `retry_queue` row automatically, so that nothing needs to opt in.

**Acceptance criteria:**
- `turn_persist.py` inserts a pending row when `_turn_floored(state)` is true.
- Insert failure never blocks turn delivery (best-effort, logged).

**Source hint:** Plan Task 3.

### Story 1.4: Capture And Backfill The Sent Telegram Message Reference

As the retry loop, I want the real Telegram `message_id` of a floored turn's sent message, so that a later successful retry can edit it in place.

**Acceptance criteria:**
- `_send_part` returns the sent `telegram.Message` instead of discarding it.
- A floored turn's send backfills `channel_chat_id`/`channel_message_id` on its `retry_queue` row.

**Source hint:** Plan Task 4. Note: this fixes a real pre-existing gap (message reference was always discarded, not just for floors).

### Story 1.5: RetryActuator

As the retry loop, I want one shared function that re-runs a failed goal steered away from what already failed, so cron and manual retry share identical behavior.

**Acceptance criteria:**
- `attempt_retry(row)` re-invokes the pipeline via the same pattern `goal_execution.py` already uses.
- Success edits the original message in place and marks the row `completed`.
- Failure marks the attempt and, at 3 failures, sends one notification and stops.

**Source hint:** Plan Task 5.

### Story 1.6: Scheduler Sweep

As the platform, I want a 1-minute cron sweep of due `retry_queue` rows, so failures retry themselves without user action.

**Acceptance criteria:**
- `RetrySweepHandler` retries every due row via `RetryActuator`.
- One failing row never stops the sweep from processing the rest.

**Source hint:** Plan Task 6.

### Story 1.7: Manual "Do It Again"

As a user, I want to say "do it again" and have it retry my prior failed ask with awareness of what already failed, so I don't have to wait for the cron tick.

**Acceptance criteria:**
- `RetryIntentClassifier` is LLM-based (no hardcoded keyword list).
- Retry intent, when detected against an open pending row, dispatches `attempt_retry` immediately from `triage.run`.

**Source hint:** Plan Task 7.

### Story 1.8: End-To-End Regression Pass

As the platform, I want one test proving the full loop (floor → row → sweep → success → edit), so the pieces are proven to work together, not just in isolation.

**Acceptance criteria:**
- End-to-end test passes.
- Full targeted suite + `ruff` + `mypy` clean.

**Source hint:** Plan Task 8.

---

## Epic 2: Approach Rating Buttons

**Objective:** Every substantial final Telegram answer gets a Like/Dislike button row rating the turn's *approach* (not output content). Likes feed DNA evolution's existing positive-signal query; dislikes are recorded but excluded from it.

**Business value:** Gives explicit, low-friction feedback on tool choice/reasoning quality, separate from rating the output content itself.

**Source documents (full detail — read these, this epic file is a stub):**
- Spec: `docs/superpowers/specs/2026-07-12-approach-rating-buttons-design.md`
- Implementation plan (7 tasks, complete code, TDD steps): `docs/superpowers/plans/2026-07-12-approach-rating-buttons.md`

**Dependencies:** Both this epic and Epic 3 modify `consolidate.py`'s tail — see each plan's Global Constraints for the ordering note (token line must be appended before the rating keyboard attaches). No dependency on Epic 1.

### Story 2.1: Approach Rating Column Migration

As the platform, I want an `approach_rating` column on `task_outcomes`, so a vote has somewhere durable to land.

**Acceptance criteria:** idempotent migration adds the nullable column.
**Source hint:** Plan Task 1.

### Story 2.2: TaskOutcomeStore.set_approach_rating

As the pipeline, I want one method to write a vote by trace_id, so callers don't write raw SQL.

**Acceptance criteria:** updates existing row; returns `False` (never raises) when no row exists for that trace_id.
**Source hint:** Plan Task 2.

### Story 2.3: Wire Dislike Exclusion Into DNA Attribution

As the platform, I want disliked-approach outcomes excluded from DNA evolution's positive-signal query, so the positive-only-learning principle holds.

**Acceptance criteria:** `dna_attribution.py`'s filter excludes `approach_rating == "negative"`; positive and unrated outcomes unaffected.
**Source hint:** Plan Task 3.

### Story 2.4: Approach Rating Keyboard + Callback Handler

As a user, I want to tap Like/Dislike and see my vote reflected, so I know it registered.

**Acceptance criteria:** `ApproachRatingTracker`/`ApproachRatingCallbackHandler` mirror `consent.py`'s pattern; edit-in-place on vote; graceful no-op on missing trace.
**Source hint:** Plan Task 4.

### Story 2.5: Attach Keyboard To Qualifying Answers

As the platform, I want the rating keyboard attached only to substantial final answers, so trivial replies aren't cluttered.

**Acceptance criteria:** ≥200 char, non-floor answers get `raw_keyboard` populated; others don't.
**Source hint:** Plan Task 5.

### Story 2.6: Send Raw Keyboard, Backfill Tracker, Register Callback

As the platform, I want the adapter to actually send the keyboard and know which message to edit later, so votes work end-to-end.

**Acceptance criteria:** `send()` sends `raw_keyboard` chunks and backfills `(chat_id, message_id)`; `apr` prefix registered on the callback router.
**Source hint:** Plan Task 6.

### Story 2.7: End-To-End Regression Pass

As the platform, I want one test proving the full loop, so the pieces are proven to work together.

**Acceptance criteria:** e2e test passes; full targeted suite + ruff + mypy clean.
**Source hint:** Plan Task 7.

---

## Epic 3: Token Usage Display

**Objective:** Every final Telegram answer shows total input/output tokens spent that turn, reusing existing `cost_records` capture — no new schema.

**Business value:** Cost transparency for the user, zero new tracking overhead.

**Source documents (full detail — read these, this epic file is a stub):**
- Spec: `docs/superpowers/specs/2026-07-12-token-usage-display-design.md`
- Implementation plan (3 tasks, complete code, TDD steps): `docs/superpowers/plans/2026-07-12-token-usage-display.md`

**Dependencies:** Shares `consolidate.py`'s tail with Epic 2 — this epic's token-line append must run BEFORE Epic 2's keyboard attach (see plan's Global Constraints). No dependency on Epic 1.

### Story 3.1: get_turn_token_totals Query

As the pipeline, I want summed input/output tokens for a trace_id, so I can display what a turn actually cost.

**Acceptance criteria:** sums multiple `cost_records` rows for one trace; returns `None` when no rows exist.
**Source hint:** Plan Task 1.

### Story 3.2: Append Token Line In Consolidate

As a user, I want to see token counts on my answer, so I have cost visibility.

**Acceptance criteria:** token line appended to qualifying final answers; nothing appended when no records exist or on floor chunks.
**Source hint:** Plan Task 2.

### Story 3.3: End-To-End Regression Pass

As the platform, I want one test proving the full loop, so the pieces are proven to work together.

**Acceptance criteria:** e2e test passes; full targeted suite + ruff + mypy clean.
**Source hint:** Plan Task 3.
