-- D01.7 — rename the conversation-domain `session_id` column to `session_key`.
--
-- WHY: StackOwl's `session_id` has always held the LANE (a chat id that never
-- changes), which is the reference platform' `session_key`. Migration 0092 introduced the real
-- lane/incarnation split, where `session_id` means THIS INCARNATION of a lane and
-- changes on every reset. Leaving seven tables using the old name for the new
-- concept's identifier would have guaranteed the exact confusion DOC_STANDARD
-- calls the single most common source of bugs in this subsystem.
--
-- VALUES ARE UNCHANGED. This migration renames a column, nothing more; every row
-- keeps the bare chat id it already held. The composite lane key
-- ("owl:brain:telegram:dm:12345") starts being written when ingress resolution
-- lands, and historical rows are documented as un-backfillable — the owl and
-- channel that would be needed to reconstruct a composite key were never stored.
--
-- NOT TOUCHED:
--   * `parliament_sessions.session_id` — a debate id. A different concept that
--     happens to share the word.
--   * `sessions.session_id` (migration 0092) — already the incarnation.
--
-- SQLite rewrites dependent index definitions automatically on RENAME COLUMN
-- (>= 3.25), so idx_conversations_session, ix_cost_records_session,
-- idx_task_outcomes_session, idx_retry_queue_session and
-- idx_message_ledger_session all follow the column without being re-created here.
--
-- Idempotency is provided by the runner: schema_migrations records the version and
-- `MigrationRunner._apply` skips an already-applied file. ALTER TABLE ... RENAME
-- COLUMN has no IF EXISTS form, so a guard cannot be expressed in the SQL itself.

ALTER TABLE conversations   RENAME COLUMN session_id TO session_key;
ALTER TABLE thread_registry RENAME COLUMN session_id TO session_key;
ALTER TABLE cost_records    RENAME COLUMN session_id TO session_key;
ALTER TABLE task_outcomes   RENAME COLUMN session_id TO session_key;
ALTER TABLE turn_decisions  RENAME COLUMN session_id TO session_key;
ALTER TABLE retry_queue     RENAME COLUMN session_id TO session_key;
ALTER TABLE message_ledger  RENAME COLUMN session_id TO session_key;
