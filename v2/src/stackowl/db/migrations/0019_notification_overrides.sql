-- Story 7.4: Notification overrides — per-session quiet-hours suppression rules.
CREATE TABLE IF NOT EXISTS notification_overrides (
    override_id TEXT NOT NULL PRIMARY KEY,
    start_time  TEXT NOT NULL,
    end_time    TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    category    TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_notification_overrides_expires
    ON notification_overrides(expires_at);
