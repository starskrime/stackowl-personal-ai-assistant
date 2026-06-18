-- Story 8.4: onboarding — record one-time UI tips already shown to the user.
CREATE TABLE IF NOT EXISTS onboarding (
    key       TEXT NOT NULL PRIMARY KEY,
    shown_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
