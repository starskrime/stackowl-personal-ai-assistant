-- Migration 0086 — epic execution columns (Task #4 of the coding-capability
-- build plan; see docs/superpowers/specs/2026-07-13-epic-execution-design.md).
--
-- Column-only change: no new table, no index. NULL/empty for every existing
-- row and every caller that doesn't set them — a plain objective (repo unset)
-- stays byte-identical.

ALTER TABLE objectives ADD COLUMN repo TEXT;
ALTER TABLE objectives ADD COLUMN integration_branch TEXT;
ALTER TABLE objectives ADD COLUMN base_branch TEXT;

ALTER TABLE objective_subgoals ADD COLUMN depends_on TEXT;
ALTER TABLE objective_subgoals ADD COLUMN worktree_path TEXT;
ALTER TABLE objective_subgoals ADD COLUMN story_branch TEXT;
