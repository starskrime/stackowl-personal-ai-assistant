CREATE TABLE IF NOT EXISTS onboarding_events (
    event       TEXT NOT NULL UNIQUE,
    recorded_at TEXT NOT NULL
);
