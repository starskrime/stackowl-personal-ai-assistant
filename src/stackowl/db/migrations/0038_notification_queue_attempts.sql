-- 0038: bound digest-flush retries with a per-row attempt counter.
-- A permanently-undeliverable batched row would otherwise re-select every
-- digest tick forever, hot-looping the adapter and growing the queue without
-- bound. The digest increments attempts on each failed transport and
-- dead-letters the row past the cap. Idempotency is provided by the
-- MigrationRunner version-skip (no column guard needed here).
ALTER TABLE notification_queue ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
