-- Story 2.8 -- the record_ref target `consent.decided` needs that has no
-- existing owning store (AD-4: "a target with no owning store gains a table
-- by migration"). Mirrors migration 0146's `turn_action_records` precedent:
-- ConsentPolicy._finalize's EXISTING synchronous `audit_logger.append()`
-- write stays untouched (Design Notes: converting it to the async
-- chain_append_via_pool path is deliberately out of scope for this story on
-- a live production consent gate), so this new table -- and the fresh
-- transaction that inserts into it -- is the "no existing async DB write to
-- join" fallback, same shape as `turn_action_records`.
--
-- One row per consent decision. `decision` is the closed "allow"/"deny"
-- value; `channel` and `reason` are the same bounded labels the journal
-- event's own `attrs` carries, so a reader of THOSE TWO FIELDS never needs a
-- second lookup. `scope`/`category` are NOT duplicated here -- they live
-- only in the journal event's `attrs` (`ConsentDecisionAttrs`), same as
-- every other attrs-only field this story's tables deliberately keep out of
-- their own SQL columns.
--
-- NOTE: no literal semicolon inside these comments -- the runner's _split_sql
-- treats one as a statement break (see db/migrations/runner.py).

CREATE TABLE IF NOT EXISTS consent_decision_records (
    id              TEXT PRIMARY KEY,
    tool_name       TEXT NOT NULL,
    channel         TEXT NOT NULL,
    reason          TEXT NOT NULL,
    decision        TEXT NOT NULL,
    occurred_at     TEXT NOT NULL
);

-- Backs store_cadence.py's `_hot("consent_decision_records", "occurred_at")`
-- declaration -- written on every consequential-action consent decision of
-- ordinary turn traffic, the same HOT cadence as `journal_events` itself,
-- which this table's own writer also writes to in the same transaction.
CREATE INDEX IF NOT EXISTS idx_consent_decision_records_occurred_at
    ON consent_decision_records (occurred_at);

-- A HOT-cadence, ever-growing table, and `tool_name` is exactly the column
-- an operator or a test filters by first ("what did this tool's consent
-- history look like") -- without this a `WHERE tool_name = ?` degrades to a
-- full scan as the table grows.
CREATE INDEX IF NOT EXISTS idx_consent_decision_records_tool_name
    ON consent_decision_records (tool_name);
