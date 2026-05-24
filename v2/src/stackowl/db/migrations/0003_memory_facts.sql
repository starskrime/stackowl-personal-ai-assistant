-- Knowledge earning: staged → reinforced → committed → pruned lifecycle.
CREATE TABLE IF NOT EXISTS memory_facts (
    id                     TEXT NOT NULL PRIMARY KEY,
    content                TEXT NOT NULL,
    source_conversation_id TEXT REFERENCES conversations(id),
    confidence             REAL NOT NULL DEFAULT 0.5,
    stage                  TEXT NOT NULL DEFAULT 'staged'
        CHECK (stage IN ('staged', 'reinforced', 'committed', 'pruned')),
    reinforcement_count    INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    pruned_at              TEXT
);

CREATE INDEX IF NOT EXISTS idx_memory_facts_stage ON memory_facts(stage)
