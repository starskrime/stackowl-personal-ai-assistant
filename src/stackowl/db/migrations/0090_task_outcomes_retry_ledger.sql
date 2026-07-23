-- Migration 0090: add retry_lineage_id / retry_event_count to task_outcomes
-- Workstream B (retry-ledger observability), Phase 5. retry_lineage_id
-- correlates every attempt of the SAME underlying retry (app-level
-- RetryActuator, or its own provider-layer cascade) despite trace_id churn
-- per attempt; NULL for a normal (non-retry) turn. retry_event_count is how
-- many provider-layer retry/circuit-breaker events (circuit_open_skip,
-- rate_limit_penalty, cooldown, same_tier_retry, tier_escalation) fired
-- during THIS turn — 0 for the overwhelming common case.
ALTER TABLE task_outcomes ADD COLUMN retry_lineage_id TEXT;
ALTER TABLE task_outcomes ADD COLUMN retry_event_count INTEGER NOT NULL DEFAULT 0;
