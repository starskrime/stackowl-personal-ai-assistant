-- Pellets: structured knowledge artifacts produced by Parliament sessions.
CREATE TABLE IF NOT EXISTS pellets (
    id              TEXT NOT NULL PRIMARY KEY,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    tags            TEXT NOT NULL DEFAULT '[]',
    source_session  TEXT,
    embedding_path  TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pellets_created ON pellets(created_at)
