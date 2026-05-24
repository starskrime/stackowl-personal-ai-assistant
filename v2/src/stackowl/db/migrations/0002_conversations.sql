-- Conversation sessions and individual messages.
CREATE TABLE IF NOT EXISTS conversations (
    id            TEXT NOT NULL PRIMARY KEY,
    session_id    TEXT NOT NULL,
    owl_name      TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    message_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT    NOT NULL PRIMARY KEY,
    conversation_id TEXT    NOT NULL REFERENCES conversations(id),
    role            TEXT    NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content         TEXT    NOT NULL,
    token_count     INTEGER,
    model           TEXT,
    created_at      TEXT    NOT NULL,
    trace_id        TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id)
