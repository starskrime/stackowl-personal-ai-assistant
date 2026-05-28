-- Migration 0029 task_outcomes
-- One row per completed pipeline run. Captured synchronously in
-- AsyncioBackend/LangGraphBackend after the step loop finishes. quality_score
-- starts NULL and gets filled in asynchronously by the critic_scorer handler.
-- Telemetry, NOT knowledge — separate from staged_facts and audit_log on
-- purpose per the Commit 1 pre-implementation audit.
-- NOTE no semicolons in comments per migration runner gotcha.

CREATE TABLE IF NOT EXISTS task_outcomes (
    outcome_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id         TEXT    NOT NULL,
    session_id       TEXT    NOT NULL,
    owl_name         TEXT    NOT NULL,
    channel          TEXT    NOT NULL,
    success          INTEGER NOT NULL,
    latency_ms       REAL    NOT NULL,
    tool_call_count  INTEGER NOT NULL DEFAULT 0,
    failure_class    TEXT,
    quality_score    REAL,
    step_durations   TEXT    NOT NULL DEFAULT '{}',
    input_text       TEXT    NOT NULL DEFAULT '',
    response_text    TEXT    NOT NULL DEFAULT '',
    captured_at      REAL    NOT NULL,
    scored_at        REAL,
    UNIQUE(trace_id)
);

CREATE INDEX IF NOT EXISTS idx_task_outcomes_session ON task_outcomes(session_id);
CREATE INDEX IF NOT EXISTS idx_task_outcomes_owl ON task_outcomes(owl_name);
CREATE INDEX IF NOT EXISTS idx_task_outcomes_quality ON task_outcomes(quality_score);
CREATE INDEX IF NOT EXISTS idx_task_outcomes_pending ON task_outcomes(scored_at) WHERE scored_at IS NULL;
