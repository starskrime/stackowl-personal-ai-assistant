-- Migration 0118 — an owl gets ONE home, and it is SQLite.
--
-- WHY. Bakir, 2026-08-16: "Nothing should live in memory, everything in md or
-- sqlite. No data duplication md file or sqlite."
--
-- MEASURED before writing this, because the objection deserves numbers rather
-- than agreement. One owl currently lives in FOUR places:
--
--   * `~/.stackowl/stackowl.yaml`  — 12 manifests (name, display_name, role,
--     system_prompt, tools). This is the AUTHORITATIVE copy today: the registry
--     is built by OwlRegistry.from_settings(settings.owls), and every write goes
--     through OwlsCommand._upsert_to_yaml (5 call sites).
--   * `owl_dna`                    — the same 12 owls' traits.
--   * `owl_dna_authored`           — 17 rows, so 5 orphans for owls that no
--     longer exist. Append-only with no decay.
--   * `owl_profiles`               — 0 rows. The table exists and NOTHING writes
--     it: a reader with no writer, the mirror image of the defect this codebase
--     keeps finding.
--
--   ...plus OwlRegistry._owls, an in-memory dict rebuilt at every boot.
--
-- The split is not academic. It is what produced the rename bug on 2026-08-16:
-- `display_name` was written correctly to the YAML and read by nobody on the path
-- whose behaviour it was meant to change.
--
-- WHY ONE JSON DOCUMENT AND NOT A COLUMN PER FIELD. OwlAgentManifest carries
-- nested models (`trigger`, `bounds`, `creation_ceiling`) and grows over time. A
-- column per pydantic field is a second copy of the schema that drifts the moment
-- the model changes — "two copies of one rule", which this tree has paid for
-- repeatedly. `manifest_json` is therefore the SINGLE source, and the scalar
-- columns beside it are a derived index written in the same statement by the same
-- writer, so they cannot disagree with it. This mirrors the `skills` table, which
-- already pairs `manifest_json` with denormalised columns.
--
-- WHAT THIS MIGRATION DOES AND DOES NOT DO. It creates the table only. The 12
-- live owls are seeded from the YAML at boot by the owl assembly (seed-if-empty,
-- idempotent), because the data lives in a file that SQL cannot read. Nothing is
-- deleted from the YAML here: the seed must be observed to have worked on the
-- real machine before anything is removed.
--
-- IDEMPOTENT: CREATE TABLE / CREATE INDEX IF NOT EXISTS, per the runner's
-- one-transaction-per-migration contract. No VACUUM (migration 0112 failed that
-- way).

CREATE TABLE IF NOT EXISTS owls (
    -- The routing slug. PRIMARY KEY because the registry is keyed by it and two
    -- owls answering to one name is the collision owl_build already guards.
    name            TEXT PRIMARY KEY,
    -- Derived index columns. Written from manifest_json by the same writer, never
    -- edited independently — read them to LIST, read manifest_json to LOAD.
    display_name    TEXT NOT NULL DEFAULT '',
    role            TEXT NOT NULL DEFAULT '',
    lifecycle       TEXT NOT NULL DEFAULT 'on_demand',
    origin          TEXT NOT NULL DEFAULT '',
    -- The single source of truth for the owl. OwlAgentManifest.model_dump_json().
    manifest_json   TEXT NOT NULL,
    -- Multi-tenant, consistent with every other owned table in this schema.
    owner_id        TEXT NOT NULL DEFAULT 'principal-default',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- The boot path lists every owl for one owner; the scheduler resolves scheduled
-- owls specifically. Both are covered without scanning the manifest blobs.
CREATE INDEX IF NOT EXISTS idx_owls_owner ON owls(owner_id);
CREATE INDEX IF NOT EXISTS idx_owls_lifecycle ON owls(lifecycle);
