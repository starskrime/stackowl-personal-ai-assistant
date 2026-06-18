-- Scheduler jobs and run history (idempotency-key based, at-least-once delivery).
CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT NOT NULL PRIMARY KEY,
    handler_name    TEXT NOT NULL,
    schedule        TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    last_run_at     TEXT,
    next_run_at     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    retry_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(next_run_at, status);

CREATE TABLE IF NOT EXISTS job_runs (
    run_id          TEXT NOT NULL PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES jobs(job_id),
    idempotency_key TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'completed',
    duration_ms     REAL,
    ran_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_runs_idempotency ON job_runs(idempotency_key, status)
