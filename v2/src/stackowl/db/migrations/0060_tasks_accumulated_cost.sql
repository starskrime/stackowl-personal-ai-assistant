-- Migration 0060 durable-task accumulated cost (PROV-3 / F093).
--
-- The BudgetGovernor cost ceiling read the in-memory per-trace cost ledger,
-- which RESETS to 0 on a durable resume. A parked+resumed task therefore
-- restarted cost accounting from 0 each attempt and could spend max_cost_usd
-- PER attempt without a cumulative bound.
--
-- This column persists the cumulative USD spend across ALL attempts of a durable
-- task on its row. The durable executor advances it each checkpointed iteration
-- and seeds the next resume's governor with it, so the cost ceiling holds across
-- the whole task lifetime, not per attempt.
--
-- Additive only: existing rows default to 0.0 (no prior spend recorded), so a
-- task created before this migration simply starts cumulative accounting now.
-- NOTE no semicolons inside comments per the runner split gotcha.

ALTER TABLE tasks ADD COLUMN accumulated_cost_usd REAL NOT NULL DEFAULT 0.0;
