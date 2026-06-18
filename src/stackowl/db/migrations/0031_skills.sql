-- Migration 0031 skills index + audit
-- Unified Skill index (Learning Commit 3, sub-phase 3a). FILES are the source
-- of truth — every row here mirrors a directory under
-- ~/.stackowl/workspace/skills/<source>/<name>/. This index just caches the
-- manifest fields needed for fast lookup, ordering, and the agent's
-- learning bookkeeping (success_rate / n_executions / embedding).
-- Audit captures every write so /skill diff and /skill restore work.
-- NOTE no semicolons in comments per migration runner gotcha.

CREATE TABLE IF NOT EXISTS skills (
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
    UNIQUE(source, name)
);

CREATE INDEX IF NOT EXISTS idx_skills_source ON skills(source);
CREATE INDEX IF NOT EXISTS idx_skills_enabled ON skills(enabled);
CREATE INDEX IF NOT EXISTS idx_skills_success_rate ON skills(success_rate);

CREATE TABLE IF NOT EXISTS skill_audit (
    audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id     INTEGER,
    skill_name   TEXT    NOT NULL,
    source       TEXT    NOT NULL,
    op           TEXT    NOT NULL,
    actor        TEXT    NOT NULL,
    before_hash  TEXT,
    after_hash   TEXT,
    details      TEXT    NOT NULL DEFAULT '{}',
    ts           REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_audit_skill ON skill_audit(skill_id);
CREATE INDEX IF NOT EXISTS idx_skill_audit_name ON skill_audit(skill_name);
CREATE INDEX IF NOT EXISTS idx_skill_audit_ts ON skill_audit(ts);
