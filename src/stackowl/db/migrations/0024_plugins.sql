CREATE TABLE IF NOT EXISTS plugins (
    name         TEXT PRIMARY KEY,
    version      TEXT NOT NULL,
    type         TEXT NOT NULL,
    entry_point  TEXT NOT NULL,
    capabilities TEXT NOT NULL DEFAULT '[]',
    config_schema TEXT,
    description  TEXT NOT NULL DEFAULT '',
    author       TEXT,
    license      TEXT,
    installed_at REAL NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1
);
