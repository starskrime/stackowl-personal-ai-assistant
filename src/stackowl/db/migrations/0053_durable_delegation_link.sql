-- Migration 0053 durable-delegation link columns (D1, agentic-os).
--
-- Links a delegated child durable task back to its parent so the durable
-- substrate can resume delegation across a kill, return a child result to the
-- parent owl, and enforce a single execution lease. All columns are added to
-- the existing tasks table (created in 0045). The version gate runs this
-- migration exactly once, so plain ALTER TABLE ADD COLUMN is safe even though
-- SQLite lacks ADD COLUMN IF NOT EXISTS. NOTE no semicolons inside comments per
-- runner gotcha.
--
-- parent_task_id
--   NULL marks a root task. A non-NULL value is the parent task_id this child
--   was delegated from, linking child to parent within the same owner scope.
--
-- parent_owl
--   The delegating owl that minted this child, used to route the child result
--   back to the right parent owl on completion.
--
-- delegate_key
--   The parent delegate_task idempotency key the child was minted from, so a
--   replayed delegation resolves to the same existing child instead of a dup.
--
-- lease_owner
--   The single-owner execution lease holder. Only the lease owner may advance a
--   task, preventing two recoverers from running the same child concurrently.
--
-- superseded
--   Timeout tombstone. 0 is live, non-zero marks a child superseded after a
--   delegation timeout so a stale late completion is ignored.
--
-- idx_tasks_parent
--   Child-lookup by parent plus roots-only recovery scan (parent_task_id IS
--   NULL), owner-scoped to stay tenant-correct.

ALTER TABLE tasks ADD COLUMN parent_task_id TEXT;
ALTER TABLE tasks ADD COLUMN parent_owl TEXT;
ALTER TABLE tasks ADD COLUMN delegate_key TEXT;
ALTER TABLE tasks ADD COLUMN lease_owner TEXT;
ALTER TABLE tasks ADD COLUMN superseded INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(owner_id, parent_task_id);
