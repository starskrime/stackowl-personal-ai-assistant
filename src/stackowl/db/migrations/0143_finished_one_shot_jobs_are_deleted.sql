-- A finished one-shot job is DELETED. It is not parked as enabled and completed.
--
-- MEASURED 2026-09-12 on the owner's box, read-only, before writing this:
--
--   jobs with enabled = 1                       169
--   rollover_summary rows, status completed,    136   every one params.run_once = 1,
--     run_once = 1, enabled = 1                        next_run_at frozen in the past
--   job_runs rows belonging to those 136          32
--   live jobs, status pending                     33
--
-- Every reader of the schedule (scheduler health, /api/v1/schedules, the Bridge)
-- therefore reported 136 pieces of finished work as live schedules.
--
-- WHY THEY WERE KEPT, AND WHY THAT REASON WAS ALREADY STALE. Commit 465c17ac
-- (2026-08-31) parked a finished one-shot as completed to keep its job_runs
-- history. Migration 0080 had already made job_runs.job_id ON DELETE CASCADE, and
-- goal_execution one-shots already deleted themselves - two rules for one fact,
-- and the one rollover_summary followed never retired anything. The scheduler now
-- deletes a finished one-shot in the same place it records the outcome, and this
-- migration removes the rows the retired rule left behind.
--
-- THE PREDICATE, AND WHAT IT MUST NEVER MATCH.
--   * params.run_once = 1 only. A recurring job is never touched, whatever its status.
--   * status completed (the retired park state), or status failed WITH enabled = 1
--     (the terminal failure the scheduler writes once a one-shot has exhausted its
--     retries) AND a job_failed_terminal audit row naming it - the 0075 pattern. A
--     failure that was never recorded is not deleted here: the row is its only
--     record, and the scheduler records it and then retires it at runtime.
--     pause() ALSO writes status failed, but with enabled = 0 - a paused one-shot
--     is the user's decision, not finished work, and is left alone.
--   * pending, running and retrying one-shots are live work and never match.
--   * json_extract sits inside a CASE guarded by json_valid, and SQLite evaluates a
--     CASE lazily, so a row with malformed params cannot abort a customer's
--     migration - it simply does not match.
--
-- job_runs FIRST, EXPLICITLY. The migration runner's connection does not enforce
-- foreign keys (see 0080 and runner.py), so the cascade that cleans up after the
-- scheduler's own deletes does not fire here and the history rows would be orphaned.
--
-- IDEMPOTENT: a second run finds no terminal one-shot and deletes nothing. The
-- runner snapshots the whole database before applying anything pending, and it logs
-- the rows each statement changed, so the boot log carries both deleted counts.

DELETE FROM job_runs
WHERE job_id IN (
    SELECT job_id FROM jobs
    WHERE (CASE WHEN json_valid(params) THEN json_extract(params, '$.run_once') END) = 1
      AND (status = 'completed'
           OR (status = 'failed' AND enabled = 1
               AND job_id IN (SELECT target FROM audit_log
                              WHERE event_type = 'job_failed_terminal')))
);

DELETE FROM jobs
WHERE (CASE WHEN json_valid(params) THEN json_extract(params, '$.run_once') END) = 1
  AND (status = 'completed'
       OR (status = 'failed' AND enabled = 1
           AND job_id IN (SELECT target FROM audit_log
                          WHERE event_type = 'job_failed_terminal')));
