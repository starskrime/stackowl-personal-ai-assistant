-- Migration 0089 — message_ledger (universal per-message status lifecycle).
--
-- Root cause: retry_queue (migration 0082) only ever gets a row for a
-- FLOORED turn; a successful turn has no durable status record at all, and
-- the old messages table (migration 0002) has zero writers anywhere in
-- src/ — dead schema. So there is no way to answer "is this message still
-- being worked on, did it get a reply, or did it fail" for the common case.
-- This table gives every inbound message exactly that: pending at intake,
-- completed once a reply is delivered, failed if the turn floored, absorbed
-- if it was folded (STEER) into another already-running turn and never got
-- its own reply.
--
-- trace_id is the PRIMARY KEY (not an app-minted UUID like retry_queue.id):
-- a message and its turn share exactly one trace_id for the message's whole
-- life, so INSERT OR IGNORE at intake is free idempotency and every status
-- flip is a plain WHERE trace_id = ?. No foreign key to retry_queue: the two
-- tables track independent concerns (delivery status vs. retry bookkeeping)
-- and are correlated by trace_id, same convention as retry_queue itself.
--
-- chat_id (stringified — mirrors retry_queue.channel_chat_id) is the
-- fan-out delivery target (e.g. a Telegram chat_id): known upfront from
-- IngressMessage.chat_id at intake, unlike retry_queue's channel_chat_id
-- which needs an async post-send backfill. Boot recovery threads it back
-- onto PipelineState.reply_target so a redriven turn replies to the right
-- destination instead of nowhere. NULL for single-terminal channels (CLI).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS, so
-- running this file twice never errors and never duplicates a table/index.

CREATE TABLE IF NOT EXISTS message_ledger (
    trace_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    chat_id TEXT,
    input_text TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed', 'absorbed')) DEFAULT 'pending',
    failure_reason TEXT,
    owner_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_message_ledger_status ON message_ledger(status, created_at);
CREATE INDEX IF NOT EXISTS idx_message_ledger_session ON message_ledger(owner_id, session_id, status);
