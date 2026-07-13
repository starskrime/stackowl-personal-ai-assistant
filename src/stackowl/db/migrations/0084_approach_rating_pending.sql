-- Migration 0084 — approach_rating_pending (fixes the gateway/core process split).
--
-- Root cause: ApproachRatingTracker was a pure in-memory dict, constructed once
-- per process by orchestrator.py's _phase_gateway. Under runtime.split_process
-- that function runs in BOTH the gateway process and the core process, so each
-- built its OWN tracker instance. consolidate.py's record_pending (core process)
-- and the Telegram send-side backfill_message + the "apr" callback tap's
-- get_message/clear (gateway process) never observed the same object, so a
-- tapped Like/Dislike vote recorded correctly in task_outcomes but the message
-- was never edited to show "Liked"/"Disliked" (tracker.get_message always
-- missed). Both processes share the same SQLite DB file, so a DB-backed store
-- (mirroring migration 0082's retry_queue) is correct where the in-memory dict
-- was not.
--
-- Deliberately a dedicated table, NOT new columns on task_outcomes: consolidate.py
-- calls record_pending BEFORE turn_persist/_capture_outcome inserts the
-- task_outcomes row for this trace_id (consolidate is the LAST pipeline step;
-- outcome capture runs after the full pipeline, in the backend's post-run
-- block) — so no task_outcomes row reliably exists yet at record_pending time.
--
-- trace_id is the app-supplied PRIMARY KEY (one pending vote per turn).
-- owner_id scopes per principal, matching every other queue/store table.
-- chat_id/message_id start NULL and are backfilled once the Telegram send
-- resolves; a row with either NULL means "no message location yet" (mirrors
-- the old tracker's get_message() -> None semantics exactly).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS, so
-- running this file twice never errors and never duplicates a table/index.

CREATE TABLE IF NOT EXISTS approach_rating_pending (
    trace_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    text TEXT NOT NULL,
    chat_id INTEGER,
    message_id INTEGER,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_approach_rating_pending_owner ON approach_rating_pending(owner_id);
