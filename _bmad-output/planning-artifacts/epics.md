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
