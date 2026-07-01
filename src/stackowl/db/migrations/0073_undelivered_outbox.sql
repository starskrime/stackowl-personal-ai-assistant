-- Migration 0073 add undelivered_outbox for PA5(b) silent-delivery gate
-- Proactive/scheduled output that cannot be delivered NOW is, on several paths,
-- DROPPED today (deliverer transport_failed, router suppressed) — only a hash
-- audit row remains, the body is gone. That is a silent fail-open hole.
-- This durable store closes it: each silent-drop seam writes a row here, and
-- the assemble step surfaces pending rows as a banner on the user's next real
-- inbound turn, then marks them surfaced so the banner shows exactly once.
-- owner_id scopes per principal (DEFAULT_PRINCIPAL_ID in single-user).
-- identity_key is WHO to surface to cross-channel (the resolved identity, see
-- the identity unification arc). The partial index keeps the next-contact
-- read O(pending) instead of O(all-time).
-- NOTE no semicolons in comments per migration runner gotcha.

CREATE TABLE IF NOT EXISTS undelivered_outbox (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id      TEXT NOT NULL,
    identity_key  TEXT NOT NULL,
    channel       TEXT,
    category      TEXT,
    urgency       TEXT,
    body          TEXT NOT NULL,
    reason        TEXT NOT NULL,
    job_id        TEXT,
    created_at    REAL NOT NULL,
    surfaced_at   REAL
);

CREATE INDEX IF NOT EXISTS idx_undelivered_pending
    ON undelivered_outbox (owner_id, identity_key)
    WHERE surfaced_at IS NULL;
