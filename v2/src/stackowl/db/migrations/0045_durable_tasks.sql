-- Migration 0045 durable-task primitives (Stage 1 Pass 3a, agentic-os).
--
-- Adds two NEW tables for the durable execution substrate. Both are born
-- owner-scoped: each carries an owner_id column NOT NULL DEFAULT
-- 'principal-default' from birth, so no later rebuild is ever needed (unlike
-- the retrofit tables in 0043/0044). The literal 'principal-default' MUST
-- equal tenancy.principal.DEFAULT_PRINCIPAL_ID forever (SQLite forbids a
-- non-constant DEFAULT). NOTE no semicolons inside comments per runner gotcha.
--
-- tasks
--   One row per durable goal. status moves
--   pending -> running -> parked/completed/failed. thread_id is the LangGraph
--   checkpoint thread id, set by the executor in a later pass. current_step is
--   the resume cursor. created_at/updated_at are ISO-8601 strings.
--
-- side_effect_ledger
--   The exactly-once intent->commit log for side-effecting tool calls. Keyed by
--   a deterministic idempotency_key (sha256 of task_id, step_index, tool_name
--   and a canonical serialization of args). status is 'intent' then 'committed'.
--   result_blob holds the serialized tool result so a replay of an
--   already-committed step returns the recorded result WITHOUT re-execution.
--   Pure/read tool calls are NOT ledgered.
--
-- These tables are NEW, so plain CREATE TABLE IF NOT EXISTS is fully idempotent.

CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT    NOT NULL,
    owner_id     TEXT    NOT NULL DEFAULT 'principal-default',
    goal         TEXT    NOT NULL,
    status       TEXT    NOT NULL,
    current_step INTEGER NOT NULL DEFAULT 0,
    thread_id    TEXT,
    result       TEXT,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    PRIMARY KEY (owner_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_owner  ON tasks(owner_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS side_effect_ledger (
    idempotency_key TEXT    PRIMARY KEY,
    task_id         TEXT    NOT NULL,
    owner_id        TEXT    NOT NULL DEFAULT 'principal-default',
    step_index      INTEGER NOT NULL,
    tool_name       TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    result_blob     TEXT,
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sel_task  ON side_effect_ledger(task_id);
CREATE INDEX IF NOT EXISTS idx_sel_owner ON side_effect_ledger(owner_id);
