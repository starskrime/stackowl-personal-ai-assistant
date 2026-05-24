-- Story 7.1: Extend jobs table and add notification tables.
-- Each ALTER lives on its own statement because the runner splits on semicolons.
ALTER TABLE jobs ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN last_error TEXT;
ALTER TABLE jobs ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE jobs ADD COLUMN replay_missed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN primary_channel TEXT;
ALTER TABLE jobs ADD COLUMN params TEXT NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS job_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    run_at      TEXT NOT NULL,
    status      TEXT NOT NULL,
    result_text TEXT,
    duration_ms REAL
);

CREATE INDEX IF NOT EXISTS idx_job_results_job_id ON job_results(job_id, run_at);

CREATE TABLE IF NOT EXISTS notification_queue (
    notification_id TEXT NOT NULL PRIMARY KEY,
    message_hash    TEXT NOT NULL,
    urgency         TEXT NOT NULL,
    category        TEXT NOT NULL,
    channel         TEXT NOT NULL,
    job_id          TEXT,
    scheduled_for   TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_notification_queue_due
    ON notification_queue(scheduled_for);

CREATE TABLE IF NOT EXISTS notification_log (
    notification_id TEXT NOT NULL PRIMARY KEY,
    urgency         TEXT NOT NULL,
    category        TEXT NOT NULL,
    channel         TEXT NOT NULL,
    job_id          TEXT,
    delivery_status TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    delivered_at    TEXT,
    message_hash    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notification_log_created
    ON notification_log(created_at);
