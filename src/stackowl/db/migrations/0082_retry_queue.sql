-- Migration 0082 — retry_queue (Story 1.1, failure-retry-loop).
--
-- Root cause: floored turns ("I couldn't fully complete this") have no
-- durable record today, so nothing can track them for automatic retry — the
-- turn just vanishes once the floored response is sent. This table is the
-- durable record a future RetryQueueStore (Story 1.2) will write to and a
-- retry worker will poll from.
--
-- id is an app-generated UUID hex (TEXT PRIMARY KEY), not
-- INTEGER PRIMARY KEY AUTOINCREMENT, so RetryQueueStore can construct the row
-- before insert. owner_id scopes per principal, matching every other
-- queue/store table in this codebase. No foreign key to messages: a floored
-- turn intentionally never persists a messages row, so the retry record
-- stands alone, correlated by trace_id instead.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS, so
-- running this file twice never errors and never duplicates a table/index.
-- No semicolons inside comments (the migration runner splits SQL on
-- top-level ';').

CREATE TABLE IF NOT EXISTS retry_queue (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    banned_capabilities TEXT NOT NULL DEFAULT '[]',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed')),
    next_retry_at TEXT NOT NULL,
    last_error TEXT,
    channel TEXT NOT NULL DEFAULT 'telegram',
    channel_chat_id TEXT,
    channel_message_id TEXT,
    owner_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retry_queue_status_due ON retry_queue(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_retry_queue_session ON retry_queue(owner_id, session_id, status);
CREATE INDEX IF NOT EXISTS idx_retry_queue_trace ON retry_queue(trace_id);
