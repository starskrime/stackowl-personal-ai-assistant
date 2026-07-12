# Failure Retry Loop — Design

Status: approved (design phase)
Feature 1 of 3 (retry loop → like/dislike → token display)

## Problem

When the pipeline exhausts its in-turn recovery ladder (`RecoveryActuator`) and
`supervisor.synthesize_floor()` produces a terminal "I couldn't fully complete
this" message, that message is the end of the story. The user has to notice
the failure, understand what went wrong, and manually ask again — and a
manual retry today just re-runs the same goal blind, with no memory of what
already failed, so it's prone to repeating the identical mistake.

## Goal

Failed asks retry themselves automatically in the background, forced onto a
different approach than what already failed, up to a bounded number of
attempts — and a manual "do it again" uses the same failure-aware retry path
instead of a blind re-ask.

## Non-goals

- Not touching in-turn recovery (`RecoveryActuator` rung logic stays as-is).
- Not retrying every assistant message — only turns that reached the terminal
  floor response.
- Not building a general job queue — reusing the existing scheduler.

## Data model

New migration, new table `retry_queue`:

```sql
CREATE TABLE retry_queue (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id),
    conversation_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    banned_capabilities TEXT NOT NULL DEFAULT '[]',  -- JSON array, cumulative
    attempt_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed')),
    next_retry_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_retry_queue_status_due ON retry_queue(status, next_retry_at);
CREATE INDEX idx_retry_queue_conversation ON retry_queue(conversation_id, status);
```

Row is inserted at the exact point `synthesize_floor()` fires — the pipeline
already has `goal`, `failed_capability`/`attempts`, `error`, `trace_id` there.
`banned_capabilities` seeds from `attempts` (what already failed this turn).

Migration follows repo convention: idempotent, `CREATE TABLE IF NOT EXISTS`,
applied via `stackowl db migrate`.

## Retry algorithm

One shared function, `attempt_retry(row: RetryQueueRow) -> RetryOutcome`,
called by both the cron sweep and the manual trigger:

1. Re-invoke the pipeline for `row.goal`, with `row.banned_capabilities`
   excluded from tool/capability selection for that turn — forces a
   different approach than what already failed.
2. Success → `status = completed`; edit the original Telegram floor message
   in place with the real answer (message_id known from the row).
3. Failure → append the newly-failed capability to `banned_capabilities`,
   `attempt_count += 1`, `last_error` updated, `next_retry_at` bumped forward
   1 minute.
4. `attempt_count >= 3` → `status = failed`; send one Telegram notification
   ("still couldn't do X after 3 tries"); cron stops touching this row
   (query filters on `status = 'pending'`, so a `failed` row is naturally
   excluded — no separate "stop" flag needed).

## Trigger paths

- **Cron**: new `JobHandler` in `src/stackowl/scheduler/handlers/retry_sweep.py`
  (mirrors `clarify_sweep.py`), seeded on the existing 1-minute schedule
  cadence already used by `objective_driver`. Scans
  `status = 'pending' AND next_retry_at <= now`.
- **Manual**: retry intent is detected the same way `FeedbackClassifier`
  detects feedback polarity — an LLM-based classifier, not a hardcoded
  keyword list (multilingual, per repo convention). When a turn starts and
  the conversation has an open `pending` `retry_queue` row, the classifier
  checks whether the new message is asking to retry the prior failed ask
  (vs. a new unrelated request); if so, `attempt_retry` runs immediately
  instead of waiting for the cron tick. If no pending row exists, or the
  classifier says this is a fresh ask, normal turn handling proceeds
  unchanged.

## Error handling

- `attempt_retry` never raises — same "no hidden errors" rule as everywhere
  else in this repo; internal exceptions get logged
  (`log.scheduler.error(...)`) and treated as a failed attempt (case 3
  above), not a crash.
- Telegram edit-in-place failure (e.g. message too old to edit, deleted by
  user) falls back to sending a new message — same fallback pattern already
  used in `adapter.py:542` (`send_text`'s MarkdownV2→plain-text fallback).
- Cumulative `banned_capabilities` guarantees no attempt repeats an
  already-excluded capability, bounding the state space across the 3 tries.

## Testing

`tests/scheduler/test_retry_sweep.py`:
- floor response via `synthesize_floor()` creates a `pending` `retry_queue` row
  with the right `banned_capabilities` seed.
- `attempt_retry` on a row whose banned list includes the only tool that
  could serve the goal surfaces a clean failure (no infinite substitution
  loop).
- 3rd consecutive failure flips `status` to `failed` and fires exactly one
  notification (not one per attempt).
- success path flips `status` to `completed` and calls the Telegram edit
  path with the right `message_id`.
- manual retry-intent path finds and retries the latest `pending` row for a
  conversation without waiting for the cron tick.
