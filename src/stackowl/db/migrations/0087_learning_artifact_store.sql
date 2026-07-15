CREATE TABLE IF NOT EXISTS learning_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL CHECK(artifact_type IN ('dna','skill')),
    artifact_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT 'auto',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_learning_artifacts_lookup
    ON learning_artifacts (owner_id, artifact_type, artifact_id, created_at);
