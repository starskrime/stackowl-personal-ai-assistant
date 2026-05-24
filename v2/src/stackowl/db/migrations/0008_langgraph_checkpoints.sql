-- LangGraph checkpoint state for crash-resume continuity.
CREATE TABLE IF NOT EXISTS langgraph_checkpoints (
    thread_id             TEXT NOT NULL,
    checkpoint_id         TEXT NOT NULL,
    parent_checkpoint_id  TEXT,
    checkpoint_data       BLOB NOT NULL,
    metadata              TEXT NOT NULL DEFAULT '{}',
    created_at            TEXT NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_id)
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_thread ON langgraph_checkpoints(thread_id, created_at)
