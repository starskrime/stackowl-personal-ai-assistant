-- Migration 0119 — ONE loop, ONE table. Bakir's loop+graph architecture.
--
-- WHY THESE COLUMNS AND NOT A NEW TABLE. Bakir, 2026-08-17: "we should have all
-- one loop course. Everything to go to that." The tree already carries FOUR
-- overlapping work engines — `tasks` (2,415 rows, live), `retry_queue` (5,410,
-- live), `objectives`/`objective_subgoals` (0/0, dormant) and `job_queue` (0 rows,
-- ZERO references in src). Adding a fifth would be the exact duplication the rule
-- in CLAUDE.md now forbids, so this EXTENDS the live one.
--
-- `tasks` was chosen because it already owns the hard parts: crash-safe leasing
-- (`lease_owner` with a CAS claim), checkpoints, parent/child links and per-task
-- cost. What it lacked is everything below — and every one of these exists because
-- Bakir named it, not because the schema looked tidy.
--
-- THE COLUMNS, AND WHOSE REQUIREMENT EACH ONE IS:
--
--   destination / achievement / delivered_at
--       "if it's delivered to me, it means loop is completed". A task is NOT done
--       when the function returns; it is done when its outcome reached where it was
--       going. `destination` is where that is, `achievement` is what done MEANS for
--       this task, `delivered_at` is the proof. Without these, "completed" is a
--       self-report — the exact overclaim shape this platform keeps paying for.
--
--   attempt_count / max_attempts / last_error / last_failure_class
--       "if it fails, again moving back to pending and adding previous failure or
--       action details. So next loop when it picks it, it also looks: is there any
--       previous one? Yes — learn from that experience." The failure record rides
--       the row, so the next attempt is CONSTRAINED rather than blind.
--       `max_attempts` is per-row so the ceiling is configurable (default 30).
--
--   banned_capabilities
--       The STRUCTURED half of that learning. Pasting the last error text into the
--       next prompt poisons the context by attempt ten; a list of what must not be
--       tried again does not grow without bound. Same field `retry_queue` already
--       proved, carried onto the one table.
--
--   next_attempt_at
--       Backoff. Without it a failed row is re-claimed on the very next 5-second
--       tick, which turns one broken task into a hot loop.
--
--   lease_expires_at
--       The crash-reclaim half of leasing. `lease_owner` alone cannot tell "a worker
--       is holding this" from "a worker died holding this", so a row would sit
--       claimed forever and the work would leak SILENTLY — the worst failure mode,
--       because nothing reports it.
--
--   depends_on
--       The graph. "Sometimes simple tasks may need multiple small tasks... one loop
--       may need small other loops." A sub-task is another ROW with a parent and a
--       dependency list, so the graph is edges between rows rather than a second
--       system. `parent_task_id` already exists and is reused.
--
--   trigger_kind
--       "whatever triggering in the platform, it's a task" — chat, schedule,
--       subgoal, incident. Recorded so the loop can be asked what it is actually
--       serving, instead of that being inferred from the goal text.
--
--   idempotency_key
--       Thirty retries must not mean thirty side effects. A task that already
--       delivered must be recognisable as delivered before it is retried.
--
-- STATUS VOCABULARY. The live values today are 'completed' and 'failed', and both
-- keep their meaning untouched. The loop adds 'pending' (claimable), 'running'
-- (leased), 'dead_letter' (ceiling hit or permanently failed — visible and
-- escalated, never silently dropped) and 'parked' (waiting on a human or an
-- external event, so it burns no attempts). No existing row changes.
--
-- IDEMPOTENT and additive: every column is nullable or defaulted, so every existing
-- row and every existing reader is byte-identical. No VACUUM (migration 0112 failed
-- that way — the runner wraps each migration in one transaction).

ALTER TABLE tasks ADD COLUMN destination        TEXT;
ALTER TABLE tasks ADD COLUMN achievement        TEXT;
ALTER TABLE tasks ADD COLUMN delivered_at       TEXT;
ALTER TABLE tasks ADD COLUMN attempt_count      INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN max_attempts       INTEGER NOT NULL DEFAULT 30;
ALTER TABLE tasks ADD COLUMN last_error         TEXT;
ALTER TABLE tasks ADD COLUMN last_failure_class TEXT;
ALTER TABLE tasks ADD COLUMN banned_capabilities TEXT;
ALTER TABLE tasks ADD COLUMN next_attempt_at    TEXT;
ALTER TABLE tasks ADD COLUMN lease_expires_at   TEXT;
ALTER TABLE tasks ADD COLUMN depends_on         TEXT;
ALTER TABLE tasks ADD COLUMN trigger_kind       TEXT;
ALTER TABLE tasks ADD COLUMN idempotency_key    TEXT;

-- The loop's hot query is "give me claimable rows": pending, due, not superseded.
-- Without this it scans 2,400+ rows every five seconds.
CREATE INDEX IF NOT EXISTS idx_tasks_claimable
    ON tasks(status, next_attempt_at);

-- Reclaiming rows whose worker died is the other per-tick query.
CREATE INDEX IF NOT EXISTS idx_tasks_lease_expiry
    ON tasks(status, lease_expires_at);
