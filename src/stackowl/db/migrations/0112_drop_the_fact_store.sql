-- Migration 0112 — empty the fact store and drop the tables nothing fills.
--
-- WHAT THE STORE ACTUALLY HELD, measured 2026-08-08 on the live database and
-- re-measured on 2026-08-10:
--
--     107,576 committed facts
--     134,901 staged facts
--      37.1%  mentioning a trace id or failure_class — the platform's own
--             diagnostics stored as durable memory ABOUT THE USER
--      49.1%  never reinforced
--
-- and the single most-reinforced entry in the whole store was
-- "Today's date is 2026-07-15" at 157 reinforcements, three weeks stale, with
-- six variants. `reinforcement_count` measured how often a topic came up, not
-- whether it mattered: the rest of the top twelve was news headlines.
--
-- WHY IT GREW LIKE THAT. `ConversationMiner.mine_all` filtered machine lanes
-- (DEBT-35, 2026-08-04). `mine_session` did not — and the conversation-boundary
-- handler called mine_session on every rolled lane, so incident- and goal-
-- lanes kept being mined through the other door. A guard on one path and not
-- its sibling.
--
-- The extraction pipeline that wrote all of this is deleted (D08.1 stages A/B)
-- and confirmed stopped in production. Curated memory — two files, a hard
-- budget, the agent doing its own forgetting — is the replacement.
--
-- THE OPERATOR ASKED FOR THIS PLAINLY, twice: "i do not think 86431 facts is
-- good. i think it is also duplicate and trash there", then "delete it". A
-- routine pre-change database backup was taken, as before every migration.
--
-- committed_facts and its FTS index are EMPTIED, not dropped: `memory` still
-- offers search, and a table that exists and returns nothing is honest, while a
-- missing table is an error at every call site.

DELETE FROM committed_facts;
DELETE FROM committed_facts_fts;
DELETE FROM staged_facts;

-- Tables that were created and never filled. `fact_rejections` is the one worth
-- noticing: the contradiction detector had never rejected a single fact in the
-- store's entire life, which is its own verdict on that mechanism.
DROP TABLE IF EXISTS fact_rejections;
DROP TABLE IF EXISTS memory_facts;
DROP TABLE IF EXISTS pellets;

-- NO VACUUM HERE. The runner wraps each migration in a transaction and SQLite
-- refuses to VACUUM inside one — it fails the whole migration, which is how this
-- was found. Space reclamation is a maintenance concern, not a schema one, and
-- the freed pages are reused by subsequent writes regardless.
