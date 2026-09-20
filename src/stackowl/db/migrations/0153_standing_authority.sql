-- Migration 0153 -- standing authority (AD-27, Story 4.6, "Standing authority
-- is explicit, and yours alone").
--
-- standing_authority
--   The one table FR32's "owner explicitly pre-approved" can ever mean.
--   Writable only through authz/standing_authority.py's grant()/revoke()
--   (NFR30) -- never from a tool, an owl or commands/spec/ directly; both
--   writers are reached only via the authority.grant/authority.revoke
--   CommandSpecs (authz/commands.py), which are declared severity=
--   "consequential", so the EXISTING action-policy gate already forces
--   needs_step_up for every requester kind before either ever runs (FR37:
--   never self-granted, never through voice).
--
--   id            -- a fresh uuid minted at GRANT time, one per row. A
--                    revoke never inserts a new row or a new id: it UPDATEs
--                    that SAME row's revoked_at in place (see idx below).
--   scope_kind    -- closed vocabulary (authz.standing_authority.ScopeKind);
--                    only "job" today.
--   scope_id      -- the scoped entity's own id (a jobs.job_id, this story).
--   command_type  -- the declared CommandSpec.command_type this grant covers
--                    (e.g. a future irreversible delivery command, 4.8+).
--   granted_by    -- the requester_kind string that approved the grant (the
--                    CommandContext.requester_kind the authority.grant
--                    command carried once its own step-up resolved).
--   provenance    -- "granted" (an explicit authority.grant command) or
--                    "grandfathered" (Story 4.8's own migration of existing
--                    jobs, explicitly deferred from this story).
--   granted_at    -- ISO-8601 UTC.
--   revoked_at    -- NULL while active; set once by authority.revoke.
--
-- idx_standing_authority_active
--   Partial index on active grants (revoked_at IS NULL) -- find_active()'s
--   own lookup key: (scope_kind, scope_id, command_type), the exact
--   ORDER BY granted_at DESC LIMIT 1 query pattern every caller uses.
--
-- tasks.authority_grant_id
--   Declared, stored, unused beyond that THIS story -- mirrors 0151's own
--   nonce/utterance_id precedent. action_policy.decide()'s new
--   authority_grant_id parameter is proven entirely by direct unit tests
--   this story (spec Boundaries: "do not wire a live DB lookup of
--   standing_authority into submit_command/execute_command_task's dispatch
--   path"); a future story (4.7+) that dispatches an actually-irreversible,
--   autonomously-run command resolves a real grant id at task-creation time
--   and writes it here for the handler to journal.
--
-- jobs.preauthorized_command_types
--   JSON array of command-type strings a job DECLARES it needs standing
--   authority for (FR33) -- a static declaration, not a live grant (Story
--   4.6's own Design Notes: routing job creation through authority.grant's
--   own step-up today would block every job creation on a Telegram
--   approval, which no current caller expects). Turning a job's declaration
--   into a real standing_authority row is 4.7/4.8's own job. NULL on every
--   legacy row, mirrors 0151/0152's bare ADD COLUMN style.
--
-- NOTE: no literal semicolon inside these comments -- the runner's
-- _split_sql treats one as a statement break (see db/migrations/runner.py).

CREATE TABLE IF NOT EXISTS standing_authority (
    id           TEXT PRIMARY KEY,
    scope_kind   TEXT NOT NULL,
    scope_id     TEXT NOT NULL,
    command_type TEXT NOT NULL,
    granted_by   TEXT NOT NULL,
    provenance   TEXT NOT NULL DEFAULT 'granted',
    granted_at   TEXT NOT NULL,
    revoked_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_standing_authority_active
    ON standing_authority(scope_kind, scope_id, command_type)
    WHERE revoked_at IS NULL;

ALTER TABLE tasks ADD COLUMN authority_grant_id TEXT;
ALTER TABLE jobs ADD COLUMN preauthorized_command_types TEXT;
