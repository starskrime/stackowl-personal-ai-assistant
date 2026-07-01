-- Migration 0074 channel_liveness for PB0b cross-process receive-liveness (RC0).
-- RC0 the telegram receive loop died and stayed dead 30h while the health sweep
-- reported ok the whole time. The sweep runs in the CORE process, but the real
-- long-poll loop lives in the GATEWAY process, so an in-proc liveness flag can
-- NEVER be seen cross-process. This durable table is the shared signal: the
-- gateway stamps last_receive_at every ~30s while its updater is running, and
-- the core health sweep reads it and reports degraded when it goes stale.
-- channel-keyed (NOT telegram-specific) so slack/whatsapp reuse it with zero new
-- migration. No seed rows the gateway seeds its own channel row at startup.
-- last_receive_at is ISO-8601 UTC wall clock (monotonic differs per process).
-- NOTE no semicolons in comments per the migration runner split gotcha.

CREATE TABLE IF NOT EXISTS channel_liveness (
    channel          TEXT PRIMARY KEY,
    last_receive_at  TEXT NOT NULL
);
