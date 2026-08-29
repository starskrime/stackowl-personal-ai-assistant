-- Move any in-flight retry_queue rows onto the ONE loop, then retire its sweep.
--
-- 49601f50 stopped WRITING retry_queue: a floored turn now enqueues a task. This
-- finishes the collapse by moving whatever was still pending, so the second
-- engine can be deleted without stranding work.
--
-- WHY A MIGRATION AND NOT JUST A DELETE. This box has zero pending rows, so
-- dropping the sweep here would look free. A customer device mid-retry would
-- silently lose that work — the sweep would be gone and nothing else would ever
-- look at the row. "It is empty on my machine" is not a migration strategy.
--
-- ONE ROW PER SESSION, because migration 0124's partial unique index enforces
-- (owner_id, idempotency_key) among live rows. retry_queue had no such
-- constraint, so a database that predates the dedup fix can legitimately hold
-- several pending rows for one session. Taking MAX(rowid) keeps the FRESHEST ask,
-- which is the same choice the live path makes when it repoints a queued retry
-- (incident 2026-07-21: the newer ask must not be dropped). Without the GROUP BY
-- this INSERT would violate the index and abort the whole migration.
--
-- destination carries the ADDRESS, not just the channel — a bare channel name
-- makes delivery impossible, so the task would never complete and would retry for
-- ever (81f6b7ec). channel_chat_id is the address retry_queue already recorded.
--
-- attempt_count is CARRIED OVER rather than reset: a row that has already failed
-- nine times has earned those attempts, and resetting would hand it a fresh
-- unbounded-feeling budget — the very behaviour this collapse removes.
INSERT OR IGNORE INTO tasks (
    task_id, owner_id, goal, status, trigger_kind, idempotency_key,
    session_key, channel, destination, achievement,
    attempt_count, next_attempt_at, last_error, banned_capabilities,
    created_at, updated_at
)
SELECT
    'retry-migrated-' || rq.id,
    rq.owner_id,
    rq.goal,
    'pending',
    'retry',
    'retry:' || rq.session_key,
    rq.session_key,
    rq.channel,
    CASE
        WHEN rq.channel_chat_id IS NOT NULL AND rq.channel_chat_id <> ''
        THEN rq.channel || ':' || rq.channel_chat_id
        ELSE rq.channel
    END,
    'the retry''s answer is delivered to the user who asked',
    COALESCE(rq.attempt_count, 0),
    rq.next_retry_at,
    rq.last_error,
    rq.banned_capabilities,
    rq.created_at,
    rq.updated_at
FROM retry_queue rq
JOIN (
    SELECT owner_id, session_key, MAX(rowid) AS keep
    FROM retry_queue
    WHERE status = 'pending'
    GROUP BY owner_id, session_key
) newest
  ON newest.keep = rq.rowid;

-- The rows that moved are done here. Marked rather than deleted: the history is
-- evidence for anyone reconciling a device after the upgrade, and this table is
-- dropped in a later release once nothing reads it.
UPDATE retry_queue
   SET status = 'completed',
       last_error = 'migrated to the task loop (migration 0125)'
 WHERE status = 'pending';

-- Retire the every-minute sweep. Its engine has no writer and no pending rows, so
-- it would fire 1,440 times a day to find nothing.
DELETE FROM jobs WHERE handler_name = 'retry_sweep';
