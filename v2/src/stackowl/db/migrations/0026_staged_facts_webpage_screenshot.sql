-- Expand staged_facts.source_type CHECK to include 'webpage' and 'screenshot'
-- so the new Camoufox-backed web_fetch tool can auto-stage what it pulls.
-- SQLite cannot ALTER a CHECK constraint in place — rebuild the table.

CREATE TABLE staged_facts_new (
    fact_id             TEXT    NOT NULL PRIMARY KEY,
    content             TEXT    NOT NULL,
    source_type         TEXT    NOT NULL
                            CHECK (source_type IN ('conversation', 'parliament', 'manual', 'webpage', 'screenshot')),
    source_ref          TEXT    NOT NULL,
    confidence          REAL    NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    staged_at           TEXT    NOT NULL,
    reinforcement_count INTEGER NOT NULL DEFAULT 0,
    status              TEXT    NOT NULL DEFAULT 'staged'
                            CHECK (status IN ('staged', 'committed', 'rejected')),
    embedding           BLOB,
    embedding_model     TEXT
);

INSERT INTO staged_facts_new
SELECT fact_id, content, source_type, source_ref, confidence, staged_at,
       reinforcement_count, status, embedding, embedding_model
FROM staged_facts;

DROP TABLE staged_facts;

ALTER TABLE staged_facts_new RENAME TO staged_facts;

CREATE INDEX IF NOT EXISTS idx_staged_facts_status     ON staged_facts (status);
CREATE INDEX IF NOT EXISTS idx_staged_facts_source_ref ON staged_facts (source_ref)
