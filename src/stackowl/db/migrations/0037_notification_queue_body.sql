-- E7-S0: Persist the notification body so batched notifications can be
-- re-sent later by the digest flush. Previously only a content hash was
-- stored, making batched rows audit-only. Storing the body in the DB is a
-- storage decision and the hash-only rule remains a LOGGING rule. The column
-- is nullable so legacy rows (no body) degrade to audit-only flush.
--
-- Idempotency: the MigrationRunner records applied versions by filename in
-- schema_migrations and skips any already-applied version, so a re-run never
-- re-executes this ALTER. No brittle column-existence guard is needed.
ALTER TABLE notification_queue ADD COLUMN message TEXT;
