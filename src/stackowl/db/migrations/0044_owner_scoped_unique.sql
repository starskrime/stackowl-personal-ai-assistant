-- Migration 0044 owner-scoped uniqueness (Pass 2 multi-tenant correctness fix)
--
-- Migration 0043 added owner_id to user-DATA tables but LEFT their inline UNIQUE
-- constraints OWNER-BLIND. Three tables carried a global UNIQUE that omits
-- owner_id, so a second tenant's upsert (ON CONFLICT) would STOMP another
-- owner's logically-distinct row:
--   tool_heuristics  UNIQUE(tool_name, condition_kind, condition_value, predicted_outcome)
--   user_preferences UNIQUE(owner_key, key)
--   skills           UNIQUE(source, name)
--
-- This migration rebuilds each table so the constraint becomes
-- UNIQUE(owner_id, <original cols>) two owners can now hold the same logical
-- key independently, and each owner's upsert conflicts only within its own
-- scope. The three stores' ON CONFLICT(...) targets are updated to lead with
-- owner_id to match the new constraint exactly.
--
-- SAFETY (inspected before writing):
--   No table declares a real REFERENCES FK to any of the three (skill_audit
--   keeps a plain INTEGER skill_id with NO REFERENCES clause). The migration
--   runner connects with plain sqlite3.connect (PRAGMA foreign_keys defaults
--   OFF and is never enabled in runner.py the foreign_keys=ON pragma lives
--   only in the runtime pool.py, not the migration connection). The standard
--   SQLite table rebuild is therefore fully safe no FK violation on DROP and
--   no PRAGMA toggle is required inside the runner's exclusive transaction.
--
-- Each rebuild preserves the EXACT live column set/order/types/defaults (incl.
-- owner_id from 0043 and, for skills, visibility from 0043), copies all rows
-- with an explicit column list, and recreates EVERY index (including the
-- idx_<t>_owner indexes from 0043). No triggers exist on these tables.
-- The runner applies a migration exactly once (tracked in schema_migrations),
-- so the plain rebuild DDL below is correct and runs once.
-- NOTE no semicolons inside comments per migration runner gotcha.

-- ============================ tool_heuristics ============================
CREATE TABLE tool_heuristics_new (
    heuristic_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name        TEXT NOT NULL,
    condition_kind   TEXT NOT NULL,
    condition_value  TEXT NOT NULL,
    predicted_outcome TEXT NOT NULL,
    evidence_count   INTEGER NOT NULL DEFAULT 1,
    mean_quality     REAL,
    failure_class    TEXT,
    last_seen_at     REAL NOT NULL,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    owner_id         TEXT NOT NULL DEFAULT 'principal-default',
    UNIQUE(owner_id, tool_name, condition_kind, condition_value, predicted_outcome)
);

INSERT INTO tool_heuristics_new (
    heuristic_id, tool_name, condition_kind, condition_value, predicted_outcome,
    evidence_count, mean_quality, failure_class,
    last_seen_at, created_at, updated_at, owner_id
)
SELECT
    heuristic_id, tool_name, condition_kind, condition_value, predicted_outcome,
    evidence_count, mean_quality, failure_class,
    last_seen_at, created_at, updated_at, owner_id
FROM tool_heuristics;

DROP TABLE tool_heuristics;
ALTER TABLE tool_heuristics_new RENAME TO tool_heuristics;

CREATE INDEX IF NOT EXISTS idx_tool_heuristics_tool ON tool_heuristics(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_heuristics_outcome ON tool_heuristics(predicted_outcome);
CREATE INDEX IF NOT EXISTS idx_tool_heuristics_evidence ON tool_heuristics(evidence_count);
CREATE INDEX IF NOT EXISTS idx_tool_heuristics_owner ON tool_heuristics(owner_id);

-- ============================ user_preferences ============================
CREATE TABLE user_preferences_new (
    pref_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_key   TEXT    NOT NULL,
    key         TEXT    NOT NULL,
    value       TEXT    NOT NULL,
    updated_at  REAL    NOT NULL,
    owner_id    TEXT    NOT NULL DEFAULT 'principal-default',
    UNIQUE(owner_id, owner_key, key)
);

INSERT INTO user_preferences_new (
    pref_id, owner_key, key, value, updated_at, owner_id
)
SELECT
    pref_id, owner_key, key, value, updated_at, owner_id
FROM user_preferences;

DROP TABLE user_preferences;
ALTER TABLE user_preferences_new RENAME TO user_preferences;

CREATE INDEX IF NOT EXISTS idx_user_preferences_owner ON user_preferences(owner_id);
CREATE INDEX IF NOT EXISTS idx_user_preferences_owner_key ON user_preferences(owner_key);

-- ============================ skills ============================
CREATE TABLE skills_new (
    skill_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    source            TEXT    NOT NULL,
    path              TEXT    NOT NULL,
    description       TEXT    NOT NULL DEFAULT '',
    when_to_use       TEXT    NOT NULL DEFAULT '',
    version           TEXT    NOT NULL DEFAULT '0.0.0',
    enabled           INTEGER NOT NULL DEFAULT 1,
    success_rate      REAL,
    n_executions      INTEGER NOT NULL DEFAULT 0,
    parent_traces     TEXT    NOT NULL DEFAULT '[]',
    embedding         BLOB,
    embedding_model   TEXT,
    manifest_json     TEXT    NOT NULL DEFAULT '{}',
    body_text         TEXT    NOT NULL DEFAULT '',
    loaded_at         REAL    NOT NULL,
    updated_at        REAL    NOT NULL,
    owner_id          TEXT    NOT NULL DEFAULT 'principal-default',
    visibility        TEXT    NOT NULL DEFAULT 'private',
    UNIQUE(owner_id, source, name)
);

INSERT INTO skills_new (
    skill_id, name, source, path, description, when_to_use, version, enabled,
    success_rate, n_executions, parent_traces, embedding, embedding_model,
    manifest_json, body_text, loaded_at, updated_at, owner_id, visibility
)
SELECT
    skill_id, name, source, path, description, when_to_use, version, enabled,
    success_rate, n_executions, parent_traces, embedding, embedding_model,
    manifest_json, body_text, loaded_at, updated_at, owner_id, visibility
FROM skills;

DROP TABLE skills;
ALTER TABLE skills_new RENAME TO skills;

CREATE INDEX IF NOT EXISTS idx_skills_source ON skills(source);
CREATE INDEX IF NOT EXISTS idx_skills_enabled ON skills(enabled);
CREATE INDEX IF NOT EXISTS idx_skills_success_rate ON skills(success_rate);
CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_id);
