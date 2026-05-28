-- Migration 0035 tool_heuristics
-- Canonical SQLite store for tool-call heuristics mined from task_outcomes +
-- audit_log (Learning Commit 5). Each row links a tool + condition predicate
-- to a predicted outcome class with an evidence count. Embeddings + content
-- ALSO land in the LanceDB lessons table so cross-source retrieval is one
-- ANN call per feedback_use_existing_infrastructure  the SQLite row stays
-- the source of truth for the mining pipeline + audit + UX.
-- NOTE no semicolons in comments per migration runner gotcha.

CREATE TABLE IF NOT EXISTS tool_heuristics (
    heuristic_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name        TEXT NOT NULL,
    condition_kind   TEXT NOT NULL,
    condition_value  TEXT NOT NULL,
    predicted_outcome TEXT NOT NULL,
    evidence_count   INTEGER NOT NULL DEFAULT 1,
    mean_quality     REAL,
    failure_class    TEXT,
    last_seen_at     REAL NOT NULL,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    UNIQUE(tool_name, condition_kind, condition_value, predicted_outcome)
);

CREATE INDEX IF NOT EXISTS idx_tool_heuristics_tool ON tool_heuristics(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_heuristics_outcome ON tool_heuristics(predicted_outcome);
CREATE INDEX IF NOT EXISTS idx_tool_heuristics_evidence ON tool_heuristics(evidence_count);
