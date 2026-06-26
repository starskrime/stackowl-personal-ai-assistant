-- Migration 0067: goal-level acceptance criteria on sub-goals (verification B3).
--
-- The verification primitive's goal-level half. A sub-goal MAY declare an
-- ExpectedOutcome (a deterministically-observable post-condition, e.g. "a fresh
-- file must appear under DIR"). When present, the objective driver gates the
-- sub-goal's done/failed verdict on observing that outcome against reality
-- instead of "no error thrown" — catching the class where the tool itself cannot
-- self-verify (a shell no-op that exits 0). NULL (the default for every existing
-- and most new rows) keeps the legacy no-error completion path: byte-identical.
--
-- Stored as JSON (the serialized ExpectedOutcome model); NULL when undeclared.

ALTER TABLE objective_subgoals ADD COLUMN acceptance_criteria TEXT;
