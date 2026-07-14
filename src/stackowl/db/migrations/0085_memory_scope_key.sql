-- Migration 0085 — scope_key on staged_facts / committed_facts (Phase 2 of the
-- coding-capability build plan).
--
-- Gives memory a scope dimension beyond user/conversation: a fact can now carry
-- an optional scope_key (e.g. a repo path or remote) so a repo can accumulate
-- its own facts (build command, conventions, prior fixes) distinct from
-- general conversational memory. NULL (the default for every existing row and
-- every caller that doesn't set it) means "global/unscoped" — byte-identical
-- to today's behavior; recall() only filters by scope when a caller explicitly
-- asks for one.
--
-- Column-only change: no new table, no index (scope-filtering happens in
-- Python over the already-fetched candidate set, not via a SQL predicate on
-- this column, so no query plans depend on an index here yet).

ALTER TABLE staged_facts ADD COLUMN scope_key TEXT;
ALTER TABLE committed_facts ADD COLUMN scope_key TEXT;
