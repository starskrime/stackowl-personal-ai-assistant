-- Migration 0034 add dna_snapshot to task_outcomes
-- Per operator vote in Learning Commit 4 — attribution-based DNA tuning needs
-- to know what DNA was active when each outcome happened. Stored as JSON
-- object {trait_name: float}. Empty default keeps existing rows valid.
-- Populated by _capture_outcome in the pipeline backends at outcome time.
-- NOTE no semicolons in comments per migration runner gotcha.

ALTER TABLE task_outcomes ADD COLUMN dna_snapshot TEXT NOT NULL DEFAULT '{}';
