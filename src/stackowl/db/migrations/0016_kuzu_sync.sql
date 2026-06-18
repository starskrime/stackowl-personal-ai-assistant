-- Kuzu graph sync tracking (Epic 6, Story 6.5).
-- Records which committed facts have been mirrored into the Kuzu knowledge
-- graph so the KuzuSyncJobHandler can pick up from the last successful sync
-- on each run.

CREATE TABLE IF NOT EXISTS kuzu_sync_log (
    fact_id       TEXT    NOT NULL,
    synced_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    entity_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (fact_id)
)
