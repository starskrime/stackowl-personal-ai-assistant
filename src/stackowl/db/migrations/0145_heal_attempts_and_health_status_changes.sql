-- Story 2.6 -- the two record_ref targets heal.* / health.changed need that
-- have no existing owning store (AD-4: "a target with no owning store gains a
-- table by migration"). Jobs already have `jobs`/`job_runs`; heals and health
-- transitions had nothing until now.
--
-- heal_attempts: one row per heal attempt against a subsystem, updated by the
-- health sweep's heal -> verify cycle (heal.attempted / heal.healed /
-- heal.exhausted all point at the same row for one subsystem-down episode).
--
-- health_status_changes: one row per OBSERVED status transition for a
-- subsystem -- the health sweep's own source of "what was the previous status"
-- (FR84), read back as `SELECT new_status ... ORDER BY occurred_at DESC LIMIT 1`
-- rather than kept in a second in-memory cache.
--
-- NOTE: no literal semicolon inside these comments -- the runner's _split_sql
-- treats one as a statement break (see db/migrations/runner.py).

CREATE TABLE IF NOT EXISTS heal_attempts (
    id              TEXT PRIMARY KEY,
    subsystem       TEXT NOT NULL,
    status          TEXT NOT NULL,
    attempt_count   INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_heal_attempts_subsystem
    ON heal_attempts (subsystem);

CREATE TABLE IF NOT EXISTS health_status_changes (
    id                  TEXT PRIMARY KEY,
    subsystem           TEXT NOT NULL,
    previous_status     TEXT NOT NULL,
    new_status          TEXT NOT NULL,
    error_code          TEXT,
    occurred_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_health_status_changes_subsystem
    ON health_status_changes (subsystem);
