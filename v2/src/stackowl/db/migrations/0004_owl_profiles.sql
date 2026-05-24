-- Owl DNA: personality traits that evolve across conversations.
CREATE TABLE IF NOT EXISTS owl_profiles (
    name                TEXT NOT NULL PRIMARY KEY,
    manifest_path       TEXT NOT NULL,
    challenge_level     REAL NOT NULL DEFAULT 0.5,
    verbosity           REAL NOT NULL DEFAULT 0.5,
    expertise_growth    TEXT NOT NULL DEFAULT '{}',
    learned_preferences TEXT NOT NULL DEFAULT '{}',
    interaction_count   INTEGER NOT NULL DEFAULT 0,
    last_evolution_at   TEXT,
    created_at          TEXT NOT NULL
)
