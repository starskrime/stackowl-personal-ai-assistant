-- Migration 0054 durable delivery target columns on jobs (C1 proactivity).
--
-- A cron-born job (auto-seeded morning_brief, scheduled check_in) has NO live
-- session, no TraceContext and no channel at poll time, so it cannot resolve a
-- recipient. Today a target-less proactive send rides telegram's shared mutable
-- _last_chat_id and, on a fresh process, delivers to nobody. These two nullable
-- columns persist the recipient ON the job row so the DeliverySpec resolver can
-- address every cron-born send from durable state, never from request context.
--
-- The version gate runs this migration exactly once, so plain ALTER TABLE ADD
-- COLUMN is safe even though SQLite lacks ADD COLUMN IF NOT EXISTS. NOTE no
-- semicolons inside comments per the runner split-sql gotcha.
--
-- target_channels
--   Nullable JSON array of channel names the job should deliver to, e.g.
--   ["telegram"] or ["telegram","slack"]. NULL on a legacy/customer row means
--   no durable recipient was stamped (resolver returns no pairs -> caller
--   records undeliverable, never delivered).
--
-- target_addresses
--   Nullable JSON object mapping channel name -> that channel's NATIVE
--   destination token (telegram int chat_id, slack str channel id), e.g.
--   {"telegram": 12345, "slack": "C0123"}. The native type is preserved through
--   JSON so each adapter receives its own correctly-typed token.

ALTER TABLE jobs ADD COLUMN target_channels TEXT;
ALTER TABLE jobs ADD COLUMN target_addresses TEXT;
