-- Migration 0151 -- the COMMAND task kind (AD-26, Story 4.3, "A declared
-- command runs through one door").
--
-- Every one of Epic 4's future migrated mutators (4.3, 4.7-4.10) submits
-- through ONE typed entry (commands/spec/submit.py::submit_command) that
-- enqueues a `tasks` row shaped like this, never a bespoke table -- the same
-- "extend the one loop, do not build a second engine" rule 0045/0053/0126
-- already followed for a chat turn, a delegated child and objective work.
-- All seven columns are nullable/defaulted, so every EXISTING goal-task
-- caller is byte-identical after this migration (kind defaults to 'goal').
--
-- kind
--   'goal' (the loop's existing shape, unchanged) or 'command' (this story).
--   Literal in the domain model (pipeline/durable/task.py); the column stays
--   a plain TEXT with a DEFAULT so a legacy row still reads 'goal' with no
--   backfill needed.
--
-- command_type / command_payload / command_id / requester_kind / nonce /
-- utterance_id
--   command_type names the declared CommandSpec (e.g. 'scheduling.pause_job');
--   command_payload is the ALREADY-VALIDATED payload as JSON text (mirrors
--   creation_ceiling/task_envelope's own JSON-text convention on this same
--   table); command_id is the idempotency identity a caller may re-submit
--   (idx_tasks_command_id below is what makes a retry a no-op rather than a
--   duplicate row); requester_kind is set from ingress/trace provenance
--   (authz.requester.requester_kind_from_trace), never from the payload;
--   nonce/utterance_id are declared now for voice (4.4+) and stored,
--   unused, beyond that -- see spec-4-3's Boundaries.
--
-- idx_tasks_command_id
--   Partial unique index (mirrors 0124_tasks_idempotency_key_is_enforced.sql's
--   own partial-unique shape): only rows that DO carry a command_id are
--   constrained, so a plain goal task (command_id IS NULL) never collides.
--   This is the actual mechanism behind "submit_command called twice with the
--   same command_id does not enqueue a second row" -- the second INSERT
--   raises and the caller re-SELECTs the existing row by command_id.
--
-- command_receipts
--   The idempotency guard a subsystem mutator (e.g. JobScheduler.pause)
--   writes into, in its OWN transaction, before applying its mutation --
--   AD-26: "the subsystem mutator writes command_id in its own transaction
--   so re-execution after a lease reclaim is a no-op." A second write for the
--   same command_id is an INSERT OR IGNORE no-op, which is what makes the
--   mutation short-circuit rather than repeat.
--
-- NOTE: no literal semicolon inside these comments -- the runner's
-- _split_sql treats one as a statement break (see db/migrations/runner.py).

ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'goal';
ALTER TABLE tasks ADD COLUMN command_type TEXT;
ALTER TABLE tasks ADD COLUMN command_payload TEXT;
ALTER TABLE tasks ADD COLUMN command_id TEXT;
ALTER TABLE tasks ADD COLUMN requester_kind TEXT;
ALTER TABLE tasks ADD COLUMN nonce TEXT;
ALTER TABLE tasks ADD COLUMN utterance_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_command_id
    ON tasks(command_id)
    WHERE command_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS command_receipts (
    command_id   TEXT PRIMARY KEY,
    command_type TEXT NOT NULL,
    executed_at  TEXT NOT NULL
);
