CREATE TABLE IF NOT EXISTS parliament_sessions (
    session_id    TEXT PRIMARY KEY,
    topic         TEXT NOT NULL,
    owl_names     TEXT NOT NULL,
    rounds        TEXT NOT NULL DEFAULT '[]',
    synthesis     TEXT,
    status        TEXT NOT NULL DEFAULT 'running',
    started_at    TEXT NOT NULL,
    completed_at  TEXT,
    interjections TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_parliament_sessions_started_at
    ON parliament_sessions (started_at DESC);
