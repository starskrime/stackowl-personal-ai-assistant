-- Migration 0050 — per-owl skill instruction-injection (Owl Capability arc, Story 2).
--
-- Adds four columns to the skills table to support the skill-summary layer
-- used for instruction injection at assemble time.
--
-- summary
--   Resolved condensed playbook text. Populated by the back-fill worker or
--   copied verbatim from the author-supplied override in the manifest
--   frontmatter. NULL until the back-fill run completes for a given skill.
--
-- summary_source
--   Provenance tag: "author" when the manifest supplied an explicit override,
--   "generated" when the system produced it. Lets the back-fill worker skip
--   rows that have an author-owned summary (never overwrite author intent).
--   NULL on rows that have not yet been summarised.
--
-- summary_body_hash
--   SHA-256 of (body + override + source + sanitizer_version), hex-encoded.
--   Used as a cache-invalidation key: when the hash changes the stored summary
--   is stale and should be regenerated. NULL on unsummarised rows.
--
-- tool_names
--   JSON array of the tool names registered by this skill (derived from the
--   on-disk tools/ sidecar at load time). Defaults to an empty JSON array so
--   callers can always JSON-parse the column without a NULL check.
--
-- Design choices:
--   Additive only: existing rows gain NULL or default values, no data touched.
--   tool_names NOT NULL DEFAULT '[]': always parseable as JSON, no NULL guard.
--   summary, summary_source, summary_body_hash: nullable, absent until filled.
--
-- Idempotent: the MigrationRunner records applied versions in schema_migrations
-- and skips a version already recorded, so these ADD COLUMN statements run
-- exactly once. SQLite has no ADD COLUMN IF NOT EXISTS, so the runner version
-- gate is the idempotency mechanism. No semicolons inside comments (runner
-- splits SQL on semicolons).
ALTER TABLE skills ADD COLUMN summary TEXT;
ALTER TABLE skills ADD COLUMN summary_source TEXT;
ALTER TABLE skills ADD COLUMN summary_body_hash TEXT;
ALTER TABLE skills ADD COLUMN tool_names TEXT NOT NULL DEFAULT '[]';
