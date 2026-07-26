-- D01.7 slice 3b part 5a — a fact can be AUTHORED ONCE.
--
-- WHY A TABLE REBUILD. staged_facts.source_type carries a CHECK constraint, and
-- SQLite cannot alter a CHECK in place. The whole table is therefore rebuilt to
-- widen the allowed set by one value. That is expensive-looking and is the right
-- call: the constraint is what CAUGHT this (the first attempt to stage a summary
-- failed loudly at the database instead of writing an unrecognised type that six
-- readers would have silently ignored), so it is kept and widened rather than
-- dropped.
--
-- WHY THE NEW TYPE EXISTS. The promotion gate requires CORROBORATION —
-- reinforcement_count >= 3 for most types, >= 1 for conversation_fact. That is
-- the right rule for an EXTRACTED claim: a fact derived twice is likelier true
-- than one derived once, and the miner's re-derivation is what supplies the
-- second sighting.
--
-- It is the wrong rule for an AUTHORED artifact. The rollover summary (Q17) is
-- written exactly once per conversation boundary and is never re-derived, so its
-- reinforcement_count stays 0 for ever. Under any existing source type it would
-- sit in staged_facts permanently — written, logged as written, and never
-- recalled. That is the third dormant-feature trap found in this item and the
-- only one that would have been entirely silent, because every intermediate step
-- reports success.
--
-- The promoter gate itself is widened in code (fact_promoter._ELIGIBLE_PREDICATE),
-- where the same predicate is now shared with dream_worker_helpers' stuck-eligible
-- counter instead of being duplicated and "kept in lock-step by review".
--
-- SAFETY. staged_facts has no foreign keys pointing at it (verified against the
-- live schema), the runner executes this file inside BEGIN EXCLUSIVE, and the
-- three secondary indexes are recreated below. Columns are listed explicitly in
-- the copy so a future column addition cannot silently reorder into the wrong
-- slot.

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
                            CHECK (status IN ('staged', 'committed', 'rejected')),
    embedding           BLOB,
    embedding_model     TEXT,
    owner_id            TEXT    NOT NULL DEFAULT 'principal-default',
    trust               TEXT    NOT NULL DEFAULT 'untrusted',
    scope_key           TEXT
);

INSERT INTO staged_facts_new (
    fact_id, content, source_type, source_ref, confidence, staged_at,
    reinforcement_count, status, embedding, embedding_model, owner_id, trust,
    scope_key
)
SELECT
    fact_id, content, source_type, source_ref, confidence, staged_at,
    reinforcement_count, status, embedding, embedding_model, owner_id, trust,
    scope_key
FROM staged_facts;

DROP TABLE staged_facts;

ALTER TABLE staged_facts_new RENAME TO staged_facts;

CREATE INDEX IF NOT EXISTS idx_staged_facts_status     ON staged_facts (status);
CREATE INDEX IF NOT EXISTS idx_staged_facts_source_ref ON staged_facts (source_ref);
CREATE INDEX IF NOT EXISTS idx_staged_facts_owner      ON staged_facts (owner_id);
