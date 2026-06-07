-- Migration 0051 owl_dna_authored
-- Durable per-owl AUTHORED (baseline) DNA, captured from the YAML/manifest at boot
-- before evolved DNA is hydrated. The envelope anchor and reset-dna target.
CREATE TABLE IF NOT EXISTS owl_dna_authored (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owl_name TEXT NOT NULL UNIQUE,
    challenge_level REAL NOT NULL DEFAULT 0.5,
    verbosity REAL NOT NULL DEFAULT 0.5,
    curiosity REAL NOT NULL DEFAULT 0.5,
    formality REAL NOT NULL DEFAULT 0.5,
    creativity REAL NOT NULL DEFAULT 0.5,
    precision REAL NOT NULL DEFAULT 0.5,
    owner_id TEXT NOT NULL DEFAULT 'principal-default',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_owl_dna_authored_owner ON owl_dna_authored(owner_id);
