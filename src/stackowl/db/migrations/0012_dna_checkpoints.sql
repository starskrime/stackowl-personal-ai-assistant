CREATE TABLE IF NOT EXISTS dna_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owl_name TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL UNIQUE,
    challenge_level REAL NOT NULL,
    verbosity REAL NOT NULL,
    curiosity REAL NOT NULL,
    formality REAL NOT NULL,
    creativity REAL NOT NULL,
    precision REAL NOT NULL,
    reason TEXT NOT NULL DEFAULT 'auto',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_dna_checkpoints_owl ON dna_checkpoints (owl_name, created_at);
