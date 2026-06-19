-- Migration 0064: add overclaim_blocked to task_outcomes
-- Stamped True by surface_overclaim_gate when it replaces a confident non-floor
-- draft with the honest floor (nothing delivered, tool failed/bounced). Default 0
-- keeps existing rows valid without a backfill.
ALTER TABLE task_outcomes ADD COLUMN overclaim_blocked INTEGER NOT NULL DEFAULT 0
