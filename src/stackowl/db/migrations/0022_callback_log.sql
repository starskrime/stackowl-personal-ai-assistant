CREATE TABLE IF NOT EXISTS callback_log (
    callback_id TEXT PRIMARY KEY,
    callback_data TEXT NOT NULL,
    processed_at REAL NOT NULL
);
