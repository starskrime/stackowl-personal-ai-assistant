-- Migration 0111 — record which authoring-standard version a skill meets.
--
-- D10.2 R6Q24: a skill records the version it conformed to, so a later rule
-- change re-migrates only what actually moved rather than re-validating the
-- whole catalog or grandfathering old skills into permanent fragmentation.
--
-- WHY A COLUMN AND NOT A RE-CHECK. Conformance can be recomputed from the file
-- at any time — but the migration pass is an LLM rewrite per skill, and without
-- a record of what has already been migrated every run pays that cost again for
-- work already done. This is the idempotency key for an expensive operation,
-- not a cache of a cheap answer.
--
-- DEFAULT 0 means "predates the standard", which is the honest value for every
-- row that exists when this runs: 157 of 168 live skills fail the description
-- cap alone. It is deliberately NOT defaulted to the current version — that
-- would declare the entire backlog migrated by fiat and leave the migrator with
-- nothing to do.

ALTER TABLE skills ADD COLUMN standard_version INTEGER NOT NULL DEFAULT 0;

-- The migrator scans for rows below the current version, so this is the access
-- path for every run of it.
CREATE INDEX IF NOT EXISTS idx_skills_standard_version
  ON skills (owner_id, standard_version);
