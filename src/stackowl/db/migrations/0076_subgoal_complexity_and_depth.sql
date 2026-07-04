-- Migration 0076: adaptive decomposition — sub-goal complexity + recursion depth (Task 3).
--
-- estimated_complexity: the decomposer's own per-sub-goal complexity estimate
-- (0.0-1.0), populated by the SAME decomposition LLM call that produced the
-- sub-goal's description (no extra round-trip). DEFAULT 0.0 keeps every
-- existing row -- and any decomposition reply missing the marker -- legacy-safe
-- (it never crosses the driver's recursion threshold).
--
-- decomposition_depth: how many recursive decompositions produced this
-- sub-goal; 0 = top-level (the objective's initial decomposition). The driver
-- refuses to split a sub-goal further once this reaches its
-- `_MAX_DECOMPOSITION_DEPTH` constant, so persisting the depth here is what
-- makes that cap durable across ticks/restarts rather than an in-memory-only
-- guard that a crash could reset.

ALTER TABLE objective_subgoals ADD COLUMN estimated_complexity REAL NOT NULL DEFAULT 0.0;
ALTER TABLE objective_subgoals ADD COLUMN decomposition_depth INTEGER NOT NULL DEFAULT 0;
