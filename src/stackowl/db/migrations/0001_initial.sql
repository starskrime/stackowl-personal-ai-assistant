-- Core metadata key-value store.
CREATE TABLE IF NOT EXISTS stackowl_meta (
    key        TEXT NOT NULL PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

INSERT OR IGNORE INTO stackowl_meta (key, value) VALUES ('schema_version', '0000');
INSERT OR IGNORE INTO stackowl_meta (key, value) VALUES ('created_at', datetime('now', 'utc'))
