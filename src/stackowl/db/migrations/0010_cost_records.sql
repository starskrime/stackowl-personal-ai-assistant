CREATE TABLE IF NOT EXISTS cost_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_name TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    trace_id TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cost_records_date ON cost_records (substr(recorded_at, 1, 10));
