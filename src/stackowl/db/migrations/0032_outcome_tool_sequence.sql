-- Migration 0032 add tool_sequence to task_outcomes
-- Captures the ordered list of tool names invoked during a pipeline run,
-- not just the count. Required by SkillSynthesizerHandler (Learning Commit 3
-- sub-phase 3c) which clusters successful outcomes by exact tool sequence to
-- propose new learned skills. Stored as JSON array (e.g. ["web_fetch","shell"])
-- with empty default so existing rows remain valid.
-- NOTE no semicolons in comments per migration runner gotcha.

ALTER TABLE task_outcomes ADD COLUMN tool_sequence TEXT NOT NULL DEFAULT '[]';
