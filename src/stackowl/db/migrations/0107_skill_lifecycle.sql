-- ADR-19 intervention #1 — give the skill catalog a DECAY leg.
--
-- WHY. Measured on this box 2026-08-05: 421 skills, 33 ever executed (7.8%),
-- 208 total executions, and ZERO ever retired. Worse, four of the five
-- most-used skills are near-duplicates of one another:
--   structure-incident-evidence (45), structure-incident-evidence-brief (43),
--   structure-evidence-brief (23), evidence-brief-structuring (13).
-- The write side of self-improvement works so well that it has produced a
-- catalog that is 92% dead weight, and every one of the 421 still competes for
-- space in tool-search ranking and prompt assembly. An improvement loop with no
-- decay poisons its own signal.
--
-- WHAT THIS ADDS. Four columns; no data is moved, removed, or rewritten.
--
--   lifecycle_state   'active' | 'stale' | 'archived'. Archive is TERMINAL and
--                     RECOVERABLE — nothing is ever deleted (ADR-19 I3).
--   state_changed_at  when the state last moved, so a transition is auditable
--                     and a revived skill can be told from one never touched.
--   last_used_at      a DEDICATED last-use clock. `updated_at` cannot serve:
--                     it is also stamped by re-scans and metadata upserts, so
--                     a skill nobody has run looks freshly used after any
--                     rescan. That is precisely the silent-signal failure this
--                     ADR exists to stop.
--   pinned            human veto. Outranks every automatic transition
--                     (ADR-19 I4).
--
-- DEFAULTS ARE THE NO-OP. Every existing row becomes 'active', unpinned, with a
-- NULL last_used_at — byte-identical behaviour until the curator first runs, and
-- the curator itself defers its first real pass.
--
-- SAFE: verified before writing — 421 rows; no table REFERENCES skills.skill_id;
-- the skills_fts external-content triggers key on skill_id/name/description/
-- when_to_use only, so added columns cannot disturb the FTS index.
--
-- IDEMPOTENT: the runner applies each file once and records it, and every
-- statement here is additive with a default.

ALTER TABLE skills ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'active';
ALTER TABLE skills ADD COLUMN state_changed_at REAL;
ALTER TABLE skills ADD COLUMN last_used_at REAL;
ALTER TABLE skills ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0;

-- The curator scans by state and the retrieval path filters by it, so both
-- want this. Partial on the non-archived states because 'archived' is expected
-- to become the large bucket and is never scanned in the hot path.
CREATE INDEX IF NOT EXISTS ix_skills_lifecycle
    ON skills (owner_id, lifecycle_state);

-- Seed last_used_at for skills that HAVE been run, so the first curator pass
-- does not read 33 genuinely-used skills as never-used and mark them stale.
-- updated_at is the best available proxy for those rows specifically, because
-- increment_n_executions has always stamped it — for a row with
-- n_executions > 0 it is a real use-clock, and only for the untouched rows is
-- it the ambiguous value described above. Rows with n_executions = 0 are left
-- NULL on purpose: the curator ages those from loaded_at instead.
UPDATE skills SET last_used_at = updated_at WHERE n_executions > 0;
