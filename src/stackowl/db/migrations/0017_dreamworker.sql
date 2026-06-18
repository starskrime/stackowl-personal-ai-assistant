-- DreamWorker checkpoint table (Epic 6, Story 6.6).
-- Tracks nightly consolidation runs so a crashed/interrupted DreamWorker
-- can resume from the last completed phase on the next execution.

CREATE TABLE IF NOT EXISTS dreamworker_runs (
    run_id                TEXT    NOT NULL,
    started_at            TEXT    NOT NULL,
    completed_at          TEXT,
    phase                 TEXT    NOT NULL DEFAULT 'contradiction',
    facts_processed       INTEGER NOT NULL DEFAULT 0,
    facts_promoted        INTEGER NOT NULL DEFAULT 0,
    facts_pruned          INTEGER NOT NULL DEFAULT 0,
    contradictions_found  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id)
);

CREATE INDEX IF NOT EXISTS idx_dreamworker_runs_started_at
    ON dreamworker_runs (started_at DESC)
