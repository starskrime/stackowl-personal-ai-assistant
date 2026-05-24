-- Maps session_id to LangGraph thread_id for conversation resumption.
CREATE TABLE IF NOT EXISTS thread_registry (
    session_id     TEXT NOT NULL PRIMARY KEY,
    thread_id      TEXT NOT NULL UNIQUE,
    owl_name       TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    last_active_at TEXT NOT NULL
)
