-- MEM-1 (F073) — recall blends reinforcement into ranking, so the committed
-- (long-term) fact must carry the reinforcement count it accrued while staged.
-- Idempotent shape consistent with the migration runner (no inline triggers,
-- no semicolons in comments). The DEFAULT 0 backfills every legacy row as a
-- one-off (count 0), which is correct: pre-existing facts have no recorded
-- reinforcement, so they rank as the conservative floor until re-reinforced.
ALTER TABLE committed_facts ADD COLUMN reinforcement_count INTEGER NOT NULL DEFAULT 0
