-- D01.7 slice 3a.2 — cost rows record WHICH RUN of a lane produced them.
--
-- Migration 0093 renamed this table's session_id to session_key, because that is
-- what it always held: the lane. This adds the genuinely new dimension — the
-- INCARNATION — so a cost row can be grouped by conversation RUN.
--
-- WHY IT MATTERS: the D01.6 baseline reported 10 distinct prompt hashes on a
-- single "conversation" (lane 72055773, 40 turns). Grouping by lane spans every
-- rollover that lane has ever had, so it could never have shown 1. D01.1's
-- byte-identical-prompt invariant — COUNT(DISTINCT prompt_hash) per conversation
-- == 1 — needs THIS column to be a statement about one conversation.
--
-- Empty string, not NULL: it matches session_key's existing convention in this
-- table, keeps the column NOT NULL, and reads honestly for background work that
-- never passed through ingress and therefore has a lane but no incarnation.

ALTER TABLE cost_records ADD COLUMN session_id TEXT NOT NULL DEFAULT '';

-- D01.1 will group by (session_key, session_id) on every verification run, and
-- the table is already past 61k rows.
CREATE INDEX IF NOT EXISTS ix_cost_records_incarnation
    ON cost_records (session_key, session_id);
