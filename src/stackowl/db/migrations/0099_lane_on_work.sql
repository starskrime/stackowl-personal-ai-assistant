-- D01.7 slice 3b part 6 — work records which conversation it belongs to.
--
-- WHY. Invariant I4 says a lane with work in flight is never expired. Bakir's Q12
-- extends the reference platform' single condition to FOUR, because StackOwl has autonomy
-- machinery the reference platform lacks. Two were enforceable and are enforced (a running
-- background process, a pending clarify). The other two — an in-flight DURABLE
-- TASK and an ACTIVE OBJECTIVE — could not even be ASKED, because neither table
-- recorded which conversation the work belonged to. The question "is this lane
-- busy?" had no answer to look up.
--
-- NULLABLE ON PURPOSE. 722 existing tasks and every objective predate this, and
-- their lane cannot be reconstructed — a task row carries owl_name and channel but
-- never the composite lane. NULL reads honestly as "not attributable to a lane",
-- and the busy-check treats it as such: a NULL lane never matches a real one, so
-- one legacy row cannot freeze every boundary for ever. There is a test for that
-- exact case.
--
-- NOT AN AUTHORIZATION BOUNDARY. Both tables are owner-scoped via
-- OwnedRepository, and this column is deliberately NOT part of that scoping — the
-- busy-check reads across owners on purpose. Objectives are created under
-- DEFAULT_PRINCIPAL_ID (objective_tool.py) while a lane's identity_key is the
-- PERSON, so an owner-scoped read would match nothing and invariant I4 would be a
-- silent no-op. The lane IS the scope for this question: a row carrying
-- session_key = <this lane> belongs to that conversation whichever principal id
-- happened to be stamped on it, and the check returns a boolean, never row content.

ALTER TABLE tasks      ADD COLUMN session_key TEXT;
ALTER TABLE objectives ADD COLUMN session_key TEXT;

-- The busy-check runs once per candidate lane on every five-minute sweep, and
-- both predicates filter on session_key + status.
CREATE INDEX IF NOT EXISTS idx_tasks_session      ON tasks (session_key, status);
CREATE INDEX IF NOT EXISTS idx_objectives_session ON objectives (session_key, status);
