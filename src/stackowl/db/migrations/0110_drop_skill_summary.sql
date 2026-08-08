-- Migration 0110 — drop the skill `summary` field (D09.3 / D10.2, slice 5).
--
-- WHY IT GOES. `summary` was a generated condensation of a skill's body, cached
-- so the instruction injector had something shorter than the full SKILL.md to
-- inject. D10.2 makes it redundant by construction: `description` is now capped
-- at 60 characters and `when_to_use` is a required rich field carrying the
-- retrieval signal, which is exactly what the summary was approximating.
--
-- MEASURED before removing, on the live catalog 2026-08-08: 169 of 170 skills
-- carried a generated summary averaging 324 characters. `description` +
-- `when_to_use` averages 301. The injector's existing fallback
-- (`f"{description} — {when_to_use}"`) therefore produces near-identical content,
-- so what this deletes is a per-skill LLM call at boot and three columns that
-- could drift from the fields they duplicate — not information.
--
-- Two writers to one fact is how fields drift. The database already owned
-- description and when_to_use; summary was a third, stale-able copy of both.
--
-- IRREVERSIBLE by nature (a dropped column cannot be un-dropped), which is why
-- the generated text is not worth preserving: it is derived data, reproducible
-- from the body at any time, and nothing reads it that cannot read its sources.

-- SQLite 3.35+ supports DROP COLUMN directly. Each is guarded by the runner's
-- idempotency contract: re-running a migration must not fail the boot.
ALTER TABLE skills DROP COLUMN summary;
ALTER TABLE skills DROP COLUMN summary_source;
ALTER TABLE skills DROP COLUMN summary_body_hash;

-- skills_fts is a contentless-external FTS5 table whose column list must match
-- what the application syncs. Rebuild it without `summary` and repopulate from
-- the base table in the same migration — an FTS index left describing a column
-- that no longer exists makes every keyword query a hard error, so this cannot
-- be deferred to "the next boot re-scan".
DROP TABLE IF EXISTS skills_fts;
CREATE VIRTUAL TABLE skills_fts USING fts5(name, description, when_to_use);
INSERT INTO skills_fts (rowid, name, description, when_to_use)
SELECT skill_id, name, COALESCE(description, ''), COALESCE(when_to_use, '')
FROM skills;
