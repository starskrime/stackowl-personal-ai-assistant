-- Migration 0154 -- the "restore-to" undo payload (Story 4.7, AC "undo an
-- edit within 24h restores the CAPTURED prior payload, not the forward one").
--
-- undo_payload
--   Optional JSON text a subsystem mutator's context-given path may capture
--   ALONGSIDE its normal idempotency receipt (record_command_execution),
--   holding whatever state its OWN undo needs to restore (e.g. edit_job
--   captures the job's PRIOR {schedule, goal} so undo can put them back,
--   rather than re-running the forward command). NULL is the default and
--   is what every 4.3/4.5/4.6 command (pause/resume/grant/revoke) keeps
--   writing -- undo.py falls back to the original command_payload exactly
--   as before when this column is NULL, so nothing already shipped changes
--   behavior.
--
-- NOTE: no literal semicolon inside these comments -- the runner's
-- _split_sql treats one as a statement break (see db/migrations/runner.py).

ALTER TABLE command_receipts ADD COLUMN undo_payload TEXT;
