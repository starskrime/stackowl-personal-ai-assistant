-- Reindex queue (Epic 6, Story 6.4).
-- Tracks committed fact ids that need to be (re-)pushed into the LanceDB
-- vector index. Populated by promotion / reinforcement workflows and drained
-- by a background reindexer.

CREATE TABLE IF NOT EXISTS reindex_queue (
    fact_id   TEXT NOT NULL,
    queued_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (fact_id)
)
