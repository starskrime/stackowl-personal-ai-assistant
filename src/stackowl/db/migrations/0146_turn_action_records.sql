-- Story 2.7 -- the record_ref target `model.called`/`tool.called`/
-- `delegation.hopped` need that has no existing owning store (AD-4: "a
-- target with no owning store gains a table by migration").
--
-- NAMED `turn_action_records`, NOT `turn_decisions` (the name the spec's own
-- Code Map names, echoing AD-4's "per-turn decisions" example). A table
-- named `turn_decisions` ALREADY EXISTS -- migration 0071, `session_id TEXT
-- PRIMARY KEY, trace_id, created_at REAL, decisions_json` -- an unrelated,
-- actively-read/written store backing the ADR-7 `/explain` surface
-- (`pipeline/decision_store.py::TurnDecisionStore`, one row per SESSION,
-- upserted). `CREATE TABLE IF NOT EXISTS turn_decisions (...)` with this
-- story's columns would silently no-op against that live schema, and every
-- INSERT from `journal/turn_events.py` would then fail at runtime with "no
-- such column" -- caught by the helpers' own `except Exception`, so the
-- whole feature would silently never record a single row. Renamed instead of
-- reusing the collided name; every other part of this story's design
-- (columns, index, the `record_ref` it backs) is unchanged from the spec.
--
-- One row per model call / tool call / delegation hop -- `kind` is the full
-- dotted event type (`model.called` / `tool.called` / `delegation.hopped`),
-- `identifier` the same bounded label also carried in the journal event's
-- own `target_id`/`attrs` (provider / tool_name / to_owl) so a reader never
-- needs a second lookup.
--
-- NOTE: no literal semicolon inside these comments -- the runner's _split_sql
-- treats one as a statement break (see db/migrations/runner.py).

CREATE TABLE IF NOT EXISTS turn_action_records (
    id              TEXT PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    kind            TEXT NOT NULL,
    identifier      TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    duration_ms     INTEGER,
    error_code      TEXT,
    occurred_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turn_action_records_trace_id
    ON turn_action_records (trace_id);

-- Backs store_cadence.py's `_hot("turn_action_records", "occurred_at")`
-- declaration: the health-sweep periodically runs `SELECT MAX(occurred_at)
-- FROM turn_action_records`, and this table grows unbounded at the same rate
-- as `journal_events` (which ships `idx_journal_events_type_occurred_at` for
-- exactly this reason).
CREATE INDEX IF NOT EXISTS idx_turn_action_records_occurred_at
    ON turn_action_records (occurred_at);
