-- DEBT-32 — let a mined conversation leave the mining queue.
--
-- WHY THIS EXISTS. The dream worker mines every session with staged conversation
-- turns, one LLM call each (15-35s observed live). Nothing ever removed a
-- session from that queue: the only caller of SqliteMemoryBridge.clear_session
-- is /reset. So the queue returned the same 900+ rows every run, a budgeted pass
-- re-mined the same first handful forever, and the rest were never reached.
--
-- WHY ORDERING WAS NOT ENOUGH. A first attempt ordered the queue by the newest
-- fact each session had PRODUCED, so the longest-neglected sorted first. That
-- fails for BARREN sessions — conversations the extractor finds nothing durable
-- in, which is most of them. They never acquire a timestamp, so they sort first
-- forever and are re-mined every run at full LLM cost for nothing. Measured on
-- the live database 2026-07-29: 4 of the 5 sessions mined in one run had ZERO
-- staged and ZERO committed facts.
--
-- The queue therefore has to record the ATTEMPT, not the outcome. A mined
-- session is marked, and the queue takes only unmarked rows — so a session
-- leaves because it was tried, whether or not it yielded anything.
--
-- WHY A TABLE REBUILD. SQLite cannot alter a CHECK constraint in place, and
-- `status` is constrained to ('staged', 'committed', 'rejected'). Adding 'mined'
-- means the standard rebuild. Bakir chose this over a separate mining-state
-- table with the cost stated (113,335 rows rewritten on a live database); a hot
-- backup was taken immediately before. The runner wraps every migration in
-- BEGIN EXCLUSIVE with ROLLBACK on failure, so this is atomic — the table is
-- either fully rebuilt or untouched.
--
-- WHY 'mined' AND NOT REUSING 'committed'. A conversation TURN is not a
-- committed fact; overloading that value would make the promotion pipeline's own
-- status ambiguous and quietly change what other queries mean.
--
-- SAFE FOR CONTEXT RECALL, verified before choosing this approach: every read of
-- source_type='conversation' in src/ is status-agnostic (sqlite_bridge.py's
-- recent_conversation_turns and its sibling both select on source_type +
-- source_ref only). A mined turn stays fully visible to the model; it simply
-- stops being queued for re-extraction. Pinned by
-- test_marking_mined_does_not_hide_turns_from_context.
--
-- Column definitions below are copied VERBATIM from the live schema; the only
-- change is the status CHECK.

CREATE TABLE staged_facts_new (
    fact_id             TEXT    NOT NULL PRIMARY KEY,
    content             TEXT    NOT NULL,
    source_type         TEXT    NOT NULL
                            CHECK (source_type IN ('conversation', 'conversation_fact',
                                                   'conversation_summary', 'parliament',
                                                   'manual', 'webpage', 'screenshot',
                                                   'agent_self')),
    source_ref          TEXT    NOT NULL,
    confidence          REAL    NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    staged_at           TEXT    NOT NULL,
    reinforcement_count INTEGER NOT NULL DEFAULT 0,
    status              TEXT    NOT NULL DEFAULT 'staged'
                            CHECK (status IN ('staged', 'committed', 'rejected', 'mined')),
    embedding           BLOB,
    embedding_model     TEXT,
    owner_id            TEXT    NOT NULL DEFAULT 'principal-default',
    trust               TEXT    NOT NULL DEFAULT 'untrusted',
    scope_key           TEXT
);

-- Explicit column list, not SELECT *, so a future column added to one table and
-- not the other fails loudly here instead of silently shifting values.
INSERT INTO staged_facts_new (
    fact_id, content, source_type, source_ref, confidence, staged_at,
    reinforcement_count, status, embedding, embedding_model, owner_id,
    trust, scope_key
)
SELECT
    fact_id, content, source_type, source_ref, confidence, staged_at,
    reinforcement_count, status, embedding, embedding_model, owner_id,
    trust, scope_key
FROM staged_facts;

DROP TABLE staged_facts;

ALTER TABLE staged_facts_new RENAME TO staged_facts;

-- Recreated verbatim — a rebuild drops them with the old table, and losing
-- idx_staged_facts_status would make the new queue predicate a full scan of
-- 113k rows on every dream run.
CREATE INDEX IF NOT EXISTS idx_staged_facts_status     ON staged_facts (status);
CREATE INDEX IF NOT EXISTS idx_staged_facts_source_ref ON staged_facts (source_ref);
CREATE INDEX IF NOT EXISTS idx_staged_facts_owner      ON staged_facts (owner_id);
