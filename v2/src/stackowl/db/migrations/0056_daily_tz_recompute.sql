-- Migration 0056 coordinated daily@ tz recompute (C1 / F108).
--
-- F108 changes how a daily@HH:MM job's next_run_at is computed: from "HH:MM UTC"
-- to "HH:MM in the user's IANA timezone, stored as UTC". next_run_at is part of
-- the occurrence dedup key (occurrence_key = idempotency_key@next_run_at), so a
-- daily job already seeded under the OLD UTC rule carries a stale UTC instant that
-- must be recomputed under the new tz rule -- WITHOUT an in-flight occurrence
-- double-firing.
--
-- The recompute itself is DST/tz-correct math that SQL cannot express (SQLite has
-- no ZoneInfo). The coordinated, double-fire-safe recompute is therefore performed
-- in Python by scheduler.recover() -> compute_next_run(schedule, tz=settings tz),
-- which runs ONCE at startup BEFORE the poll loop task is created (orchestrator
-- gateway phase). recover() only re-arms DUE jobs (next_run_at <= now), so a daily
-- job whose stale UTC instant is in the FUTURE would keep that instant until its
-- first firing. This migration closes that gap deterministically and safely: it
-- backdates every daily@ job's next_run_at to the epoch so the next startup's
-- recover() recomputes it tz-correctly. Backdating CANNOT cause a double-fire --
-- a completed occurrence is recorded in job_runs under its own key and
-- _mark_completed always advances next_run_at forward, and re-arming a
-- not-yet-fired daily job to a fresh tz-correct instant is a legitimate single
-- (re)schedule.
--
-- Idempotent: re-running sets the same sentinel value. Matches ONLY daily@ jobs
-- (the only schedule kind whose next_run_at semantics changed) so every cron and
-- minutes job is untouched. NOTE no semicolons inside comments per the runner
-- split gotcha.

UPDATE jobs
SET next_run_at = '1970-01-01T00:00:00+00:00'
WHERE schedule LIKE 'daily@%'
  AND status = 'pending'
  AND next_run_at <> '1970-01-01T00:00:00+00:00';
