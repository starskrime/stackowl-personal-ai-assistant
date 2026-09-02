-- A skill folded into another one is SUPERSEDED, and that is not the same fact
-- as "archived".
--
-- WHY THIS COLUMN EXISTS. `lifecycle_state` had two writers with opposite rules
-- and no way to tell their intents apart. FailureOutcomeMiner.adopt_legacy_siblings
-- folds `incident_<capability>_<failure>` into `incident_<capability>`, moves the
-- run history across, and marks the loser 'archived'. SkillCurator owns the same
-- field and revives anything whose idle clock is short — and a just-folded skill
-- is by construction freshly loaded, so its idle clock is always short.
--
-- MEASURED 2026-09-02, a daily loop:
--   2026-08-31 15:49  miner folds 4 siblings, marks them archived
--   2026-09-01 09:00  "[curator] run: exit ... revived 5"
--   2026-09-01 23:09  miner folds the SAME siblings again — and adds their run
--                     counts to the survivor a SECOND time
--                     (incident_web_fetch 3 -> 6, incident_shell 3 -> 6)
--   2026-09-02 09:00  "[curator] run: exit ... revived 5"
-- The adoption could never stick, and `n_executions` — which orders the
-- catalogue — was being inflated once per cycle.
--
-- WHAT THIS FIXES AND WHAT IT DOES NOT. The curator stays the single retirement
-- authority (D09.3 X11); it was never wrong, it was under-informed. Supersession
-- is recorded as its own fact, the curator reads it and never revives such a row,
-- and adoption guards on it so a sibling's runs can be credited exactly once.
-- Nothing is deleted here.
--
-- BACKFILL. Any learned skill named `<survivor>_<something>` whose `<survivor>`
-- also exists is a legacy sibling by definition — that IS the naming rule
-- adoption implements. Restricted to the incident_ family, which is the only one
-- the miner authors. Idempotent: re-running sets the same value.

ALTER TABLE skills ADD COLUMN superseded_by TEXT;

UPDATE skills
   SET superseded_by = (
        SELECT s2.name FROM skills AS s2
         WHERE s2.owner_id = skills.owner_id
           AND s2.source   = 'learned'
           AND s2.name    <> skills.name
           AND skills.name LIKE s2.name || '\_%' ESCAPE '\'
         ORDER BY LENGTH(s2.name) DESC
         LIMIT 1)
 WHERE source = 'learned'
   AND name LIKE 'incident\_%' ESCAPE '\'
   AND superseded_by IS NULL
   AND EXISTS (
        SELECT 1 FROM skills AS s3
         WHERE s3.owner_id = skills.owner_id
           AND s3.source   = 'learned'
           AND s3.name    <> skills.name
           AND skills.name LIKE s3.name || '\_%' ESCAPE '\');
