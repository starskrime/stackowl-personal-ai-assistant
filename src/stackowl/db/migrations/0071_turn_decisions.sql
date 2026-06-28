-- Migration 0071 add turn_decisions table for the ADR-7 /explain surface
-- Stores the per-turn DecisionLedger snapshot durably so "why did you do that?"
-- survives the gateway/core split and process restarts. One row per session
-- (PRIMARY KEY session_id, UPSERT keeps only the LATEST turn) so the table never
-- grows unbounded. decisions_json is the JSON-serialized tuple of Decision dicts.
-- NOTE no semicolons in comments per migration runner gotcha.

CREATE TABLE IF NOT EXISTS turn_decisions (
    session_id TEXT PRIMARY KEY,
    trace_id TEXT,
    created_at REAL NOT NULL,
    decisions_json TEXT NOT NULL
);
