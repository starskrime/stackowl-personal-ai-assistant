-- Migration 0080: job_runs.job_id ON DELETE CASCADE
--
-- Root cause: 4 independent call sites (owl_lifecycle.py reconcile,
-- scheduler/assembly.py retired-job cleanup, goal_execution.py, scheduler.py)
-- each do a bare `DELETE FROM jobs WHERE job_id = ?` with no job_runs cleanup.
-- job_runs.job_id REFERENCES jobs(job_id) with no ON DELETE clause (SQLite
-- default NO ACTION), and the RUNTIME pool (db/pool.py) turns PRAGMA
-- foreign_keys=ON -- so deleting a job that has ANY run history raises
-- `FOREIGN KEY constraint failed` at runtime. Confirmed live: an owl-schedule
-- reconcile pass deleting an orphaned owl's row failed this way.
--
-- job_runs is a dedup/history table only (0040's own comment: "no other table
-- references it"), so cascading its rows when the parent job is deleted is
-- semantically correct -- no other store needs a job's run history once the
-- job itself is gone. Fixing the FK once here corrects all 4 existing call
-- sites (and any future one) instead of patching each site individually.
--
-- SAFETY (mirrors 0044's rebuild pattern): the migration runner's own
-- connection does not enforce foreign_keys (see 0044's note), so this rebuild
-- is safe regardless. Preserves the exact live column set/order/types.
CREATE TABLE job_runs_new (
    run_id          TEXT NOT NULL PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'completed',
    duration_ms     REAL,
    ran_at          TEXT NOT NULL
);

INSERT INTO job_runs_new (run_id, job_id, idempotency_key, status, duration_ms, ran_at)
SELECT run_id, job_id, idempotency_key, status, duration_ms, ran_at FROM job_runs;

DROP TABLE job_runs;
ALTER TABLE job_runs_new RENAME TO job_runs;

CREATE INDEX IF NOT EXISTS idx_job_runs_idempotency ON job_runs(idempotency_key, status);
