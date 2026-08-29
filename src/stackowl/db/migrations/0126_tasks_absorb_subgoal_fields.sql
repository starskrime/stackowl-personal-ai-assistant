-- Give `tasks` the six fields a subgoal had and a task did not, so one row can
-- carry a unit of objective work end to end.
--
-- objective_subgoals duplicated 11 of its 18 columns onto tasks — including
-- STATUS, and it already carried a task_id. Every subgoal ran AS a task and then
-- mirrored the outcome back into its own row: two status columns for one piece of
-- work, which is the "second status column" the loop rules forbid. They diverged
-- on 2026-08-28, when 44 subgoals read pending/running while no task was running.
--
-- Nullable and unindexed: a chat turn or a cron task never sets any of these, and
-- they must cost nothing for the rows that do not use them.
ALTER TABLE tasks ADD COLUMN position INTEGER;
ALTER TABLE tasks ADD COLUMN verified INTEGER;
ALTER TABLE tasks ADD COLUMN estimated_complexity TEXT;
ALTER TABLE tasks ADD COLUMN decomposition_depth INTEGER;
ALTER TABLE tasks ADD COLUMN worktree_path TEXT;
ALTER TABLE tasks ADD COLUMN story_branch TEXT;
