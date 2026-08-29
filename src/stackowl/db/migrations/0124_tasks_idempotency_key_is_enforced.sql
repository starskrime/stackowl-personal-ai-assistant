-- Make `idempotency_key` MEAN something.
--
-- Bakir, 2026-08-28: "You are fixing which we do not need actually instead of
-- fixing core of issue not issue itself." The core he named is his own rule of
-- 2026-08-17 — everything is a TASK on ONE loop, and no implementation may
-- duplicate logic that already runs work. "Work to do" was living in four tables;
-- collapsing retry_queue into `tasks` is the first engine to go.
--
-- retry_queue kept "one in-flight retry per session" by hand: read the latest
-- pending row for the session, then supersede it. That existed because of two
-- live incidents — 2026-07-16, where every floored turn minted its own row and
-- each fired independently on the 1-minute sweep, reading as the agent
-- contradicting itself; and 2026-07-21, where a second floor while one was
-- pending was silently DROPPED and nothing ever retried it.
--
-- `tasks` already had an `idempotency_key` column for exactly this, AND NOTHING
-- ENFORCED IT. It was written on insert, read back on load, and no index made it
-- unique — a stored-but-unread column, which is the same defect shape as the
-- destination that named a channel and not an address. Moving the retry onto the
-- one loop while trusting an unenforced key would have reproduced incident
-- 2026-07-16 with a new table.
--
-- PARTIAL and scoped to LIVE work. A key is only unique among rows that are still
-- going to run: once a task completes, fails or is dead-lettered, the same key
-- must be reusable or a session could never retry twice. Owner-scoped so one
-- principal's key can never collide with another's.
--
-- Idempotent (IF NOT EXISTS) and safe on an existing database: any duplicate live
-- rows already present would make CREATE UNIQUE INDEX fail loudly rather than
-- silently drop data, which is the outcome we want — it is a signal to reconcile,
-- not something to paper over.
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_idempotency_live
    ON tasks (owner_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL
      AND status IN ('pending', 'running', 'recovering', 'parked');
