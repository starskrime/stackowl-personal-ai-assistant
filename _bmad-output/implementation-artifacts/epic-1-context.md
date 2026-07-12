# Epic 1 Context: Failure Retry Loop

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

When a turn ends in the terminal "I couldn't fully complete this" floor response, the failure should retry itself automatically in the background — capped at 3 attempts, each one forced onto a different capability than what already failed — and a user's "do it again" should trigger that same failure-aware retry immediately instead of a blind re-ask that repeats the identical mistake. This removes the manual retry burden from the user and stops the assistant from repeating an already-failed approach.

## Stories

- Story 1.1: Retry Queue Migration
- Story 1.2: RetryQueueStore
- Story 1.3: Insert Pending Row On Floored Turns
- Story 1.4: Capture And Backfill The Sent Telegram Message Reference
- Story 1.5: RetryActuator
- Story 1.6: Scheduler Sweep
- Story 1.7: Manual "Do It Again"
- Story 1.8: End-To-End Regression Pass

## Requirements & Constraints

- Only turns that reach the terminal floor response (`supervisor.synthesize_floor()`) get queued for retry — not every assistant message, and not in-turn recovery (`RecoveryActuator` rung logic is unchanged/out of scope).
- No general job queue is being built — retries reuse the existing scheduler.
- Retry is capped at 3 attempts total per failed ask. On the 3rd consecutive failure, send exactly one "still couldn't do X" notification and stop — no further cron touches on that row.
- Each retry attempt must be steered onto a capability genuinely different from every capability that already failed in this ask (cumulative, not just the most recent failure) — bounds the state space across the 3 tries and prevents an infinite substitution loop when the banned list already excludes the only capability that could serve the goal.
- Insert of the retry-tracking row must be best-effort: a failure to record it must never block turn delivery to the user.
- `attempt_retry` (the shared retry function used by both cron and manual trigger) must never raise — internal exceptions are logged and treated as a failed attempt, not a crash. One failing row must never stop a sweep from processing the rest of the batch.
- A successful retry edits the original floor message in place rather than sending a new message; if the message reference needed to edit isn't available yet, fall back to sending a new message (same pattern as the existing MarkdownV2→plain-text send fallback).
- Manual "do it again" detection must be LLM-based, not a hardcoded keyword list (multilingual — repo-wide convention already used by the existing feedback-polarity classifier). It only engages when the session has an open pending retry row; otherwise normal turn handling proceeds unchanged, so a genuinely new/unrelated ask is never misrouted into a retry.
- No hidden errors anywhere in the loop: every `except` block must log with full context, never silently swallow.
- Migrations must be idempotent (safe to re-run).
- Success criterion for the epic: one end-to-end test proves the full loop (floor → queued → swept → retried successfully → original message edited) works as a whole, not just as isolated units — plus the full targeted suite, ruff, and mypy stay clean.

## Technical Decisions

- New durable state: a retry-tracking table recording, per floored turn, the trace/session identifiers, the original goal, a cumulative list of capabilities already tried and banned, an attempt counter, status (`pending`/`completed`/`failed`), next-retry-due time, last error, and the channel + chat/message identifiers needed to edit the delivered message later.
- This table deliberately has **no foreign key to the messages table** — a floored turn intentionally does not persist an assistant message row (existing guard: promoting dressed-up floor prose into durable memory must not happen). The retry record stands alone, correlated by trace ID.
- The row insert is **two-phase**: (1) synchronous, in-pipeline, at the same point the floor condition is already detected — insert with status pending, channel identifiers still null; (2) asynchronous, post-send — once the channel adapter's send resolves and returns the real sent-message reference, backfill the channel chat/message identifiers onto the row. This mirrors an existing two-phase backfill convention already used elsewhere in the codebase for message-id-after-send.
- Fixing the channel adapter to actually capture and propagate the sent message object (instead of discarding it) is a real pre-existing gap that benefits future features too, not just this retry loop — the fix belongs at the root of the send path, not duplicated per caller.
- One shared retry function is called identically by both the cron sweep and the manual "do it again" trigger — same success/failure/notification/banned-capability-accumulation behavior regardless of trigger source. It re-invokes the pipeline the same way existing scheduled-goal execution already does (same state-construction + backend-run pattern) — no second/parallel way of injecting a synthetic turn.
- Capability avoidance on retry is **prompt-steered**, not a hard filter threaded through tool selection: the re-run's goal text explicitly names the banned capabilities and asks the model not to repeat them. This is a known, intentional ceiling — if soft steering proves unreliable in practice, the upgrade path is threading the banned list into real tool-selection exclusion.
- The recurring sweep runs on a 1-minute cadence, mirroring the existing scheduler cadence pattern already used for other periodic jobs. It queries only rows that are due and still pending — a `failed` (gave-up) row is naturally excluded from future sweeps by that status filter, no separate stop flag needed.
- All new persistent state goes through the existing SQLite pool / owner-scoped repository pattern already used for other stores in this codebase — no new persistence mechanism.

## Cross-Story Dependencies

- Story 1.2 (store) depends on 1.1 (table existing).
- Story 1.3 (floor insert) and Story 1.4 (message-id backfill) both depend on 1.2 and both write to the same row via different fields — 1.3 creates it, 1.4 fills in the channel identifiers afterward.
- Story 1.5 (RetryActuator) depends on 1.2 and is the shared function both 1.6 (scheduler sweep) and 1.7 (manual retry) call — 1.6 and 1.7 must not duplicate its retry/notify/banned-capability logic.
- Story 1.7 additionally depends on 1.4's message backfill being correct, since a manual retry may need to edit a message whose reference was captured via that fix.
- Story 1.8 is a regression pass across all of 1.1–1.7 and should not land until the preceding stories are individually complete.
- No dependency on any other epic — this is the first epic; Epic 2 (feedback buttons) and Epic 3 (token usage display) are unspecced and unrelated.
