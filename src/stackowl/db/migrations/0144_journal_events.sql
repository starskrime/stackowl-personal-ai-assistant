-- The journal: one append-only table for every state change and action the
-- platform records (AD-2, Bridge architecture spine). Story 2.1 wires only the
-- four durable-task lifecycle transitions through it -- enqueue, claim, finish,
-- dead-letter -- in the SAME transaction as the change they describe.
--
-- cursor is the ordering primitive every future reader (narrator, fan-out,
-- snapshot, split-mode TUI progress) resumes from -- INTEGER PRIMARY KEY
-- AUTOINCREMENT so it is monotonic and never reused, even across a delete.
--
-- attention and intensity stay NULLABLE. Story 2.2 owns the attention-policy
-- computation that populates them (AD-5) -- this migration only makes room for
-- it. record_ref is nullable JSON: {kind, locator}, pointing at the row this
-- event describes (AD-4) -- null only for an event with no single owning row.
--
-- attrs is the typed, per-event-type metadata payload (AD-3/AD-4), stored as
-- JSON text -- ids, counts, closed enums and bounded labels only, never free
-- text, and scanned by the leak guard before it ever reaches this column.
--
-- Index on (type, occurred_at): the two columns every reader filters or scans
-- by first -- "all task.dead_lettered events", "everything since T".
--
-- NOTE: no literal semicolon inside these comments -- the runner's _split_sql
-- treats one as a statement break (see db/migrations/runner.py).

CREATE TABLE IF NOT EXISTS journal_events (
    cursor          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL UNIQUE,
    type            TEXT NOT NULL,
    schema_version  INTEGER NOT NULL,
    occurred_at     TEXT NOT NULL,
    actor_kind      TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    device_id       TEXT,
    target_kind     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    attention       TEXT,
    intensity       TEXT,
    record_ref      TEXT,
    attrs           TEXT NOT NULL,
    trace_id        TEXT,
    duration_ms     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_journal_events_type_occurred_at
    ON journal_events (type, occurred_at);
