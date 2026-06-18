-- Migration 0028 user_preferences
-- Persisted per-owner key value preferences. Used by /tier and any future
-- preference-aware commands. Replaces the in-memory _tier_preferences dict.
-- NOTE no semicolons in comments per migration runner gotcha.

CREATE TABLE IF NOT EXISTS user_preferences (
    pref_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_key   TEXT    NOT NULL,
    key         TEXT    NOT NULL,
    value       TEXT    NOT NULL,
    updated_at  REAL    NOT NULL,
    UNIQUE(owner_key, key)
);

CREATE INDEX IF NOT EXISTS idx_user_preferences_owner ON user_preferences(owner_key);
