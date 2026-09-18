-- Story 2.11 -- the journal_prune job (scheduler/handlers/journal_prune.py)
-- filters DELETE FROM journal_events directly on occurred_at ("older than
-- retention_days"), never through type. Migration 0144's own
-- idx_journal_events_type_occurred_at is a (type, occurred_at) composite --
-- useful for "all task.dead_lettered events since T", but SQLite cannot use
-- a leading-type index to satisfy a bare occurred_at predicate, so the prune
-- query would otherwise scan the whole table every pass.
--
-- NOTE: no literal semicolon inside these comments -- the runner's _split_sql
-- treats one as a statement break (see db/migrations/runner.py).

CREATE INDEX IF NOT EXISTS idx_journal_events_occurred_at
    ON journal_events (occurred_at);
