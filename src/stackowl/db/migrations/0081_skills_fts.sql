-- Migration 0081 — skills_fts FTS5 keyword index (Story LAT.2, Phase 2 hybrid
-- skill-catalog retrieval).
--
-- Mirrors committed_facts_fts (0014_memory_tables.sql) exactly: a standalone
-- FTS5 table with its own text columns (no content=/content_rowid=
-- external-content clause), synced at the application layer
-- (skills/store.py) with rowid manually aligned to skills.skill_id (an
-- INTEGER PRIMARY KEY, i.e. a real rowid alias, so this alignment is exact).
--
-- Indexes only the retrieval surface (name, description, when_to_use,
-- summary) — NOT body_text, mirroring the embedding-composition precedent
-- (skills/assembly.py _BODY_EMBED_BYTES): body-text keyword matches are noisy
-- signal for "should this skill surface," matching how the DB-problem
-- convention in this repo requires migrations to be idempotent and safe to
-- re-run.
--
-- Idempotent: CREATE VIRTUAL TABLE IF NOT EXISTS + a backfill INSERT guarded
-- by NOT EXISTS per source row, so running this file twice never errors and
-- never duplicates an FTS row for a skill_id already indexed. No semicolons
-- inside comments (the migration runner splits SQL on top-level ';').

CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts USING fts5(name, description, when_to_use, summary);

INSERT INTO skills_fts (rowid, name, description, when_to_use, summary)
SELECT skill_id, name, description, when_to_use, COALESCE(summary, '')
FROM skills
WHERE enabled = 1
  AND NOT EXISTS (SELECT 1 FROM skills_fts WHERE skills_fts.rowid = skills.skill_id);
