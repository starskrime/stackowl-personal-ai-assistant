-- Migration 0065 command_sequences
-- Per-owner command-sequence learning: a lightweight first-order Markov model
-- over the slash commands an owner dispatches. The edges table counts each
-- prev to next transition; the last table remembers the owner's current
-- position so the next transition can be formed. Powers the TUI suggested
-- lane ("after A you usually do B"). Suggest-only telemetry, NEVER a boundary.
-- owner_id is the tenancy principal (DEFAULT_PRINCIPAL_ID) and owner_key is the
-- per-channel handle (CLI session, telegram:chat_id, ...), mirroring
-- user_preferences (migration 0028) so suggestions never leak across owners.
-- NOTE no semicolons in comments per migration runner gotcha.

CREATE TABLE IF NOT EXISTS command_sequence_edges (
    edge_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id         TEXT    NOT NULL DEFAULT 'principal-default',
    owner_key        TEXT    NOT NULL,
    prev_invocation  TEXT    NOT NULL,
    next_invocation  TEXT    NOT NULL,
    count            INTEGER NOT NULL DEFAULT 0,
    updated_at       REAL    NOT NULL,
    UNIQUE(owner_id, owner_key, prev_invocation, next_invocation)
);

CREATE INDEX IF NOT EXISTS idx_command_sequence_edges_lookup
    ON command_sequence_edges(owner_id, owner_key, prev_invocation);

CREATE TABLE IF NOT EXISTS command_sequence_last (
    owner_id         TEXT    NOT NULL DEFAULT 'principal-default',
    owner_key        TEXT    NOT NULL,
    last_invocation  TEXT    NOT NULL,
    updated_at       REAL    NOT NULL,
    UNIQUE(owner_id, owner_key)
);
