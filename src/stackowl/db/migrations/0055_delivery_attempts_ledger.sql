-- Migration 0055 delivery-attempt ledger (C1 / F103 exactly-once delivery).
--
-- The poller CAS (F103 dispatch half) stops two dispatchers double-firing, but
-- it does NOT make DELIVERY exactly-once across a crash: a process that sends a
-- brief and dies BEFORE the job_runs completion INSERT is replayed by recover()
-- and re-runs the handler -> a second brief reaches the user. This ledger closes
-- that gap. Before the side-effect the handler pre-records a row in 'dispatched'
-- state keyed by (job_id, occurrence_key, channel) then flips it to 'delivered'
-- or 'failed' once transport returns. On replay a 'dispatched'/'delivered' row
-- for the SAME occurrence+channel suppresses the re-send.
--
-- The key is OCCURRENCE-scoped (occurrence_key = idempotency_key@next_run_at),
-- NOT job-scoped, so the frozen-scheduler fix (migration 0040) is preserved: a
-- later scheduled instant is a fresh occurrence_key and a legitimately new
-- delivery, never deduped away.
--
-- The version gate runs this once, so CREATE TABLE without IF NOT EXISTS would
-- also be safe, but IF NOT EXISTS is used defensively. NOTE no semicolons inside
-- comments per the runner split-sql gotcha.
--
-- state
--   'dispatched' (pre-side-effect intent) -> 'delivered' (transport confirmed)
--   or 'failed' (transport gave up after retry). 'dispatched'/'delivered'
--   suppress a replay re-send. A 'failed' row permits an honest retry next run.
--
-- PRIMARY KEY (job_id, occurrence_key, channel)
--   The exactly-once dedup key. A second pre-record for the same occurrence on
--   the same channel collides and is rejected, which the writer treats as
--   "already dispatched -> skip the re-send".

CREATE TABLE IF NOT EXISTS delivery_attempts (
    job_id TEXT NOT NULL,
    occurrence_key TEXT NOT NULL,
    channel TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'dispatched',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, occurrence_key, channel)
);
