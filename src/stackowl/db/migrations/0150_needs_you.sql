-- Needs-you items: durable rows that survive a give-up event past the moment
-- its journal row scrolls past (AD-28, Story 3.1). Owned by journal/, created
-- here so `journal.record()` can open/close a row in the SAME transaction as
-- the event that triggers it (AD-24).
--
-- AD-28's full column list -- this story writes only a SUBSET of it.
-- waiter_kind/waiter_id/expires_at/answer exist for Story 3.2's public
-- resolver (version/digest refusal, waiter delivery, expiry sweep); this
-- story's minimal internal resolver (journal/needs_you.py) only ever sets
-- resolved_cursor/resolved_by. version defaults to 1 and is not yet bumped by
-- anything -- Story 3.2 is the first writer of a second version.
--
-- The partial unique index on dedupe_key, scoped to UNRESOLVED rows, is the
-- ONLY thing enforcing "one open item per target" (spec Boundaries: "no
-- application-level check-then-insert race") -- mirrors
-- 0124_tasks_idempotency_key_is_enforced.sql's own partial-unique shape. A
-- resolved item's dedupe_key is free to be reused by a LATER give-up for the
-- same target -- reopening is exactly what should be allowed once the first
-- incident is closed.
--
-- The second index supports the open-set query (AD-28: "every unresolved
-- item, ordered by intensity then opened_cursor").
--
-- NOTE: no literal semicolon inside these comments -- the runner's _split_sql
-- treats one as a statement break (see db/migrations/runner.py).

CREATE TABLE IF NOT EXISTS needs_you (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    intensity       TEXT NOT NULL,
    record_ref      TEXT,
    dedupe_key      TEXT NOT NULL,
    waiter_kind     TEXT,
    waiter_id       TEXT,
    expires_at      TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    opened_cursor   INTEGER NOT NULL,
    resolved_cursor INTEGER,
    answer          TEXT,
    resolved_by     TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_needs_you_dedupe_key_open
    ON needs_you (dedupe_key)
    WHERE resolved_cursor IS NULL;

CREATE INDEX IF NOT EXISTS idx_needs_you_open_set
    ON needs_you (resolved_cursor, intensity, opened_cursor);
