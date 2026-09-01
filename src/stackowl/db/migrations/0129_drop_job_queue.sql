-- Migration 0129 — delete job_queue, a table nothing has ever used.
--
-- Bakir, 2026-09-01: "whatever retired should be deleted from code and we should
-- never have dead code." This is the least ambiguous instance in the tree.
--
-- MEASURED before dropping, which is the only precondition that matters:
--   * ZERO references in src/, tests/ and scripts/ — the name appears nowhere
--     outside migrations. 0005 creates it; 0043 and 0119 only MENTION it in
--     comments while describing the engines they were reconciling.
--   * ZERO rows in the live database.
-- So there is no writer to remove first (the usual failure — migration 0125
-- deleted the retry_sweep row at 00:31:02 and scheduler assembly re-seeded it at
-- 00:31:33, every boot, for thirty-one seconds) and no reader to break.
--
-- WHY IT MATTERED WHILE IT SAT THERE. CLAUDE.md records four overlapping work
-- engines as a standing hazard — `tasks` (live), `retry_queue` (live),
-- `objectives`/`objective_subgoals` (dormant) and this one. A schema for a
-- fourth queue is an invitation to build against it, and 0119's own comment
-- shows the cost: every reconciliation of "which engine runs work here" has had
-- to stop and account for a table that never ran anything.
--
-- 0005 IS NOT DELETED, deliberately, and this is the one exception to the
-- delete-it rule. Migrations are an append-only ledger replayed in order by every
-- existing database; removing a past one rewrites history rather than the tree.
-- The ledger records that the table existed and that it is now gone.

DROP INDEX IF EXISTS idx_job_queue_status;
DROP INDEX IF EXISTS idx_job_queue_idempotency;
DROP TABLE IF EXISTS job_queue;
