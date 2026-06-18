-- STEER-5/F113 — track a job's retry slot SEPARATELY from its recurring cadence.
--
-- The failure-retry path used to overwrite ``next_run_at`` with now+5min,
-- clobbering a recurring job's canonical schedule (a daily@08:00 brief that
-- failed would retry at 08:05/08:10 and lose its real slot). ``retry_at`` holds
-- the retry instant; ``next_run_at`` stays the canonical recurring cadence,
-- untouched by retries. The poll selects a job due on EITHER slot. NULL retry_at
-- means "no retry pending" — the steady state for a healthy job.
--
-- Idempotent: guarded so re-applying on a DB that already has the column is a
-- no-op (SQLite has no ADD COLUMN IF NOT EXISTS, so the runner tracks this in
-- schema_migrations; the column add runs exactly once).
ALTER TABLE jobs ADD COLUMN retry_at TEXT;

-- A partial index so the poll's ``retry_at <= now`` arm is cheap and only ever
-- touches the small set of jobs with a pending retry.
CREATE INDEX IF NOT EXISTS idx_jobs_retry_due ON jobs(retry_at) WHERE retry_at IS NOT NULL;
