-- Migration 0068: bounded sub-goal retry budget + honest verification disposition.
--
-- Two S1 reliability fixes for the autonomous objectives driver:
--
-- F-40 (retry budget): a single transient sub-goal stumble used to flip the whole
-- objective to `blocked` permanently (a blocked objective is never re-picked,
-- since the driver scans status='active' only). `attempts` is a per-sub-goal
-- counter: on a failure the driver increments it and, while it stays under a small
-- ceiling, leaves the sub-goal `pending` so the next tick retries it — escalating
-- to `blocked` only once the budget is exhausted. This is OPERATIONAL retry state,
-- not a learned lesson (positive-only-learning is respected — nothing is mined
-- from these counts). DEFAULT 0 keeps every existing row on the legacy path.
--
-- F-42 (honest verification): when a sub-goal completed with NO declared
-- acceptance criterion (and the optional LLM deriver is off), it used to be marked
-- `done` purely because no error was thrown — self-asserted, not verified. The
-- nullable tri-state `verified` records the honest disposition: 1 = the declared
-- post-condition was observed against reality; 0 = completed but UNVERIFIED (no
-- criterion to check); NULL = legacy / not yet evaluated. A `done` sub-goal with
-- verified=0 is "completed but unverified" — completion is no longer over-claimed.

ALTER TABLE objective_subgoals ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE objective_subgoals ADD COLUMN verified INTEGER;  -- tri-state: 1 / 0 / NULL
