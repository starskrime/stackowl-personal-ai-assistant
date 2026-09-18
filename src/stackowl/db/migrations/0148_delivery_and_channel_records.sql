-- Story 2.9 -- the record_ref target `delivery.attempted`/`provider.rerouted`
-- and `channel.message_received` need that has no existing owning store
-- (AD-4: "a target with no owning store gains a table by migration").
-- Mirrors migration 0146's `turn_action_records` / 0147's
-- `consent_decision_records` precedent: none of this story's THREE call
-- sites (`ProactiveDeliverer.deliver`/`transport`, `_maybe_reroute`, and the
-- 5 gateway receive loops) has an existing state mutation of its own to
-- piggyback a journal write onto, so each new helper opens its OWN
-- transaction and inserts here.
--
-- `delivery_records` carries ONE row per `delivery.attempted` OR
-- `provider.rerouted` event -- `kind` is the dotted event type, matching
-- `turn_action_records.kind`'s own shape. `channel` is always the
-- ORIGINALLY-addressed channel for `delivery.attempted` and the
-- fallback-TO channel for `provider.rerouted` (see
-- `journal/delivery_events.py`'s own Design Notes reference). `notification_id`
-- is nullable -- `transport()`'s digest-flush call site has no `Notification`
-- in scope (Boundaries: "category=None, job_id=None").
--
-- `channel_ingress_records` carries one row per `channel.message_received`
-- event, written through the GATEWAY's own `DbPool` (AD-9) -- `session_key`
-- is the channel-scoped conversation key, never a native chat id (AD-4).
--
-- NOTE: no literal semicolon inside these comments -- the runner's _split_sql
-- treats one as a statement break (see db/migrations/runner.py).

CREATE TABLE IF NOT EXISTS delivery_records (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    channel         TEXT NOT NULL,
    notification_id TEXT,
    outcome         TEXT NOT NULL,
    occurred_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_delivery_records_occurred_at
    ON delivery_records (occurred_at);

CREATE INDEX IF NOT EXISTS idx_delivery_records_notification_id
    ON delivery_records (notification_id);

CREATE INDEX IF NOT EXISTS idx_delivery_records_kind
    ON delivery_records (kind);

CREATE TABLE IF NOT EXISTS channel_ingress_records (
    id              TEXT PRIMARY KEY,
    channel         TEXT NOT NULL,
    session_key     TEXT NOT NULL,
    occurred_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_channel_ingress_records_occurred_at
    ON channel_ingress_records (occurred_at);

CREATE INDEX IF NOT EXISTS idx_channel_ingress_records_session_key
    ON channel_ingress_records (session_key);
