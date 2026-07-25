-- D01.7 — session lifecycle. The lane/incarnation split StackOwl has never had.
--
-- session_key is the LANE: deterministic, derived from owl + channel + chat, and
-- it never changes (invariant I1). It is the primary key because there is exactly
-- one current incarnation per lane.
--
-- session_id is THIS INCARNATION. A reset keeps the key and mints a new id;
-- ids are never reused (invariant I2). Old incarnations are not stored here —
-- their transcripts live in `messages`/`conversations` and stay queryable, which
-- is Bakir's Q11 answer: keep the transcript, prune the record.

CREATE TABLE IF NOT EXISTS sessions (
    session_key        TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL,
    owl_name           TEXT NOT NULL,
    channel            TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    turn_count         INTEGER NOT NULL DEFAULT 0,

    -- State flags, evaluated in this order on every inbound message (invariant I3):
    -- suspended -> resume_pending -> policy expiry -> carry on.
    suspended          INTEGER NOT NULL DEFAULT 0,
    resume_pending     INTEGER NOT NULL DEFAULT 0,
    resume_reason      TEXT,
    was_auto_reset     INTEGER NOT NULL DEFAULT 0,
    auto_reset_reason  TEXT,
    is_fresh_reset     INTEGER NOT NULL DEFAULT 0,
    expiry_finalized   INTEGER NOT NULL DEFAULT 0,
    -- Per-lane, so one poisoned conversation cannot take the others down (Q7).
    restart_failures   INTEGER NOT NULL DEFAULT 0,

    owner_id           TEXT NOT NULL DEFAULT 'default'
);

-- The expiry sweeper scans by last activity; without this it is a full scan on
-- every pass, and the sweeper runs forever.
CREATE INDEX IF NOT EXISTS ix_sessions_updated ON sessions (updated_at);
-- Cost and metrics group by session_id (migration 0091), so the reverse lookup
-- id -> lane must be cheap.
CREATE INDEX IF NOT EXISTS ix_sessions_session_id ON sessions (session_id);
