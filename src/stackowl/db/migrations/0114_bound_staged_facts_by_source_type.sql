-- Migration 0114 — apply the per-source-type bound to the existing backlog (I7).
--
-- WHAT WAS UNBOUNDED, measured on the live database 2026-08-14:
--
--     source_type            rows   distinct source_refs   max rows per ref
--     agent_self            2,971            2,971                1
--     conversation          2,741              599               60
--     webpage                  10                9                2
--     conversation_summary      6                5                2
--
-- D08.1 bounded the conversation buffer after retiring the fact pipeline that
-- used to consume it. That bound is scoped `source_type = 'conversation' AND
-- source_ref = ?` — correct for conversation, because one session must never
-- evict another's history, and USELESS for everything else: `agent_self` carries
-- the turn's trace_id as its source_ref, so every row has a unique ref and a
-- per-ref trim keeping the newest N matches nothing, forever.
--
-- So the growth D08.1 stopped in one place continued in another. The writer that
-- produced most of these — one low-trust fact per failed turn, ~1,400 a day —
-- was itself removed in a4d89954 once it was found to write into a store with no
-- reader. This migration clears what it left behind, and
-- `SqliteMemoryBridge._trim_source_type` keeps every type bounded from now on,
-- trimming in the same path as the insert so no separate actuator can silently
-- stop working.
--
-- WHAT IS KEPT AND WHY. The newest 50 rows of each non-conversation type. These
-- rows have no rich reader — every other staged_facts SELECT in the bridge
-- filters `source_type = 'conversation'` — and surface only through
-- `list_staged` for an id-prefix lookup from the `memory` tool. The tail is
-- forensic, not functional. `conversation` is untouched here: it is bounded per
-- session, which this must not override.
--
-- THE 50 IS NOT A MAGIC NUMBER, and it is not free-standing either: it must equal
-- `_TURN_HISTORY_FLOOR` in memory/sqlite_bridge.py. SQL cannot import it, so the
-- two copies are pinned by
-- tests/memory/test_staged_facts_bound_all_types.py::test_the_migration_cap_matches_the_code_constant,
-- which fails if either side drifts.
--
-- IDEMPOTENT: re-running deletes nothing, because each type is already at or
-- below the cap. NO VACUUM — migration 0112 failed on `cannot VACUUM from within
-- a transaction` (the runner wraps each migration in one), and space reclamation
-- is a maintenance concern rather than a schema one.
--
-- Backed up first, as before every data step in this programme:
-- ~/.stackowl/backups/pre-i7-staged-bound-20260814-090118.db (922MB).

DELETE FROM staged_facts
 WHERE source_type <> 'conversation'
   AND fact_id NOT IN (
       SELECT fact_id
         FROM (
              SELECT fact_id,
                     ROW_NUMBER() OVER (
                         PARTITION BY source_type
                         ORDER BY staged_at DESC
                     ) AS rn
                FROM staged_facts
               WHERE source_type <> 'conversation'
              )
        WHERE rn <= 50
   );
