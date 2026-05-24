-- Memory knowledge pipeline tables (Epic 6, Story 6.2).
-- The migration runner splits SQL on semicolons, so this file deliberately
-- avoids any inline trigger bodies and any semicolons inside SQL comments.

CREATE TABLE IF NOT EXISTS staged_facts (
    fact_id             TEXT    NOT NULL PRIMARY KEY,
    content             TEXT    NOT NULL,
    source_type         TEXT    NOT NULL
                            CHECK (source_type IN ('conversation', 'parliament', 'manual')),
    source_ref          TEXT    NOT NULL,
    confidence          REAL    NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    staged_at           TEXT    NOT NULL,
    reinforcement_count INTEGER NOT NULL DEFAULT 0,
    status              TEXT    NOT NULL DEFAULT 'staged'
                            CHECK (status IN ('staged', 'committed', 'rejected')),
    embedding           BLOB,
    embedding_model     TEXT
);

CREATE INDEX IF NOT EXISTS idx_staged_facts_status     ON staged_facts (status);
CREATE INDEX IF NOT EXISTS idx_staged_facts_source_ref ON staged_facts (source_ref);

CREATE TABLE IF NOT EXISTS committed_facts (
    fact_id         TEXT    NOT NULL PRIMARY KEY,
    content         TEXT    NOT NULL,
    embedding       BLOB    NOT NULL,
    embedding_model TEXT    NOT NULL,
    committed_at    TEXT    NOT NULL,
    source_type     TEXT    NOT NULL,
    source_ref      TEXT    NOT NULL,
    tags            TEXT    NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_committed_facts_committed_at ON committed_facts (committed_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS committed_facts_fts USING fts5(content);

CREATE TABLE IF NOT EXISTS fact_rejections (
    rejection_id TEXT NOT NULL PRIMARY KEY,
    fact_id      TEXT NOT NULL,
    reason       TEXT,
    rejected_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id   TEXT NOT NULL PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor      TEXT NOT NULL DEFAULT 'system',
    target     TEXT NOT NULL,
    timestamp  TEXT NOT NULL,
    details    TEXT NOT NULL DEFAULT '{}'
)
