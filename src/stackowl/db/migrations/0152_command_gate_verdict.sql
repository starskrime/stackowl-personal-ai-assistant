-- Migration 0152 -- the action-policy gate's durable resume marker (AD-27/
-- AD-28, Story 4.4, "One action-policy gate decides").
--
-- gate_verdict
--   Nullable TEXT, mirrors 0151's own bare-ADD-COLUMN style (every existing
--   row reads NULL with no backfill needed). `store.park_for_decision` sets
--   it to the gate's own outcome ("needs_approval"/"needs_step_up") when a
--   COMMAND task parks awaiting a decision; `store.
--   resume_command_after_answer` overwrites it to "approved" once the row's
--   bound Needs-you item is answered "approved". `execute.
--   execute_command_task` reads `gate_verdict == 'approved'` to skip
--   straight to the deterministic handler on resume -- no re-decision, and
--   no second Needs-you item, since the item's dedupe_key is already
--   resolved and free by the time the row is re-claimed.
--
-- NOTE: no literal semicolon inside these comments -- the runner's
-- _split_sql treats one as a statement break (see db/migrations/runner.py).

ALTER TABLE tasks ADD COLUMN gate_verdict TEXT;
