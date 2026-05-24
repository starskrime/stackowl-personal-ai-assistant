-- Story 7.5: webhook event log — receipt audit trail for /webhook/{source}.
CREATE TABLE IF NOT EXISTS webhook_events_log (
    event_id    TEXT NOT NULL PRIMARY KEY,
    source      TEXT NOT NULL,
    received_at TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'enqueued'
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_source
    ON webhook_events_log (source, received_at);
