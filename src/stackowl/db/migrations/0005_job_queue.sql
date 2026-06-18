-- Persistent job queue for the JobScheduler (idempotent, at-least-once delivery).
CREATE TABLE IF NOT EXISTS job_queue (
    id               TEXT    NOT NULL PRIMARY KEY,
    job_type         TEXT    NOT NULL,
    payload          TEXT    NOT NULL DEFAULT '{}',
    status           TEXT    NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    priority         INTEGER NOT NULL DEFAULT 0,
    scheduled_at     TEXT    NOT NULL,
    started_at       TEXT,
    completed_at     TEXT,
    error_message    TEXT,
    retry_count      INTEGER NOT NULL DEFAULT 0,
    max_retries      INTEGER NOT NULL DEFAULT 3,
    idempotency_key  TEXT    UNIQUE,
    created_at       TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_queue_status ON job_queue(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_job_queue_idempotency ON job_queue(idempotency_key)
