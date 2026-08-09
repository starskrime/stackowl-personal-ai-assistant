-- D01.1 — the system prompt is built ONCE per session and reused verbatim.
--
-- WHY THIS TABLE EXISTS. assemble.run() rebuilds the whole system prompt on every
-- turn. The only enabled provider here is an OpenAI-protocol gateway, where prefix
-- caching is AUTOMATIC on a byte-identical prefix — so a prompt that changes each
-- turn forfeits that discount silently, with no marker to blame and no error to
-- notice. It is also a latency cost: a model-window probe, an embedding similarity
-- pass and several DB reads sit on the critical path of every reply, before the
-- model is called at all.
--
-- Measured before building (2026-07-27): 3 distinct prompt_hash across 8 turns on
-- real conversations. The prompt is rebuilt every turn but only DIFFERS at some,
-- and the parts that move are the ones that vary with the query — per-turn memory
-- recall and the relevance-scored skills block.
--
-- WHY PERSISTED AND NOT AN IN-MEMORY CACHE. the reference platform keeps an LRU of live agent
-- objects because their gateway restarts rarely. StackOwl's core os.execv's itself
-- on every code change, so an in-memory cache would be discarded continuously —
-- during exactly the development in which stable measurements matter most. This is
-- a deliberate divergence from the reference platform, recorded in designs/D01.1.md.
--
-- WHY THE KEY IS (session_key, owl_name). Switching owl means a different persona
-- and therefore a different prompt, a distinction the reference platform never has to make because
-- they run one agent. Not hypothetical: the staged RCA drives three owls
-- (rca_gatherer, hypothesis, verifier) against ONE incident session_key, which the
-- live logs show as three different persona_len values on the same lane.
--
-- WHY session_id IS STAMPED RATHER THAN PART OF THE KEY. Storing the INCARNATION
-- the prompt was built for makes invariant I6 self-enforcing: after a rollover
-- mints a new session_id the stored row no longer matches and the next turn cold-
-- builds, with no invalidation job, no listener and no way to forget. Keeping it
-- out of the primary key is what holds this at ONE row per (lane, owl) instead of
-- accumulating a row per incarnation forever. Same reasoning as D01.7's
-- summary_enqueued_for, where recording the incarnation rather than a timestamp is
-- what made the question answerable from the row itself.
--
-- NO owner_id, DELIBERATELY. The parent `sessions` table has none: D01.7 scopes a
-- lane by identity_key, and a prompt table inventing a different scoping model
-- would be the more confusing choice.

CREATE TABLE IF NOT EXISTS session_prompts (
    session_key  TEXT    NOT NULL,
    owl_name     TEXT    NOT NULL,
    session_id   TEXT    NOT NULL,
    prompt_text  TEXT    NOT NULL,
    prompt_hash  TEXT    NOT NULL,
    model_window INTEGER,
    built_at     TEXT    NOT NULL,
    PRIMARY KEY (session_key, owl_name)
);
