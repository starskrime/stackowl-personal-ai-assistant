-- audit_log schema fix.
--
-- Migration 0014 created a basic audit_log. Migration 0023 tried to redefine
-- it with the integrity_hash column but used CREATE TABLE IF NOT EXISTS, so
-- the redefinition silently no-opped. AuditLogger.append then failed on every
-- write because the column it needed never existed.
--
-- Since every prior INSERT failed, there is no data to preserve. We drop and
-- recreate cleanly. The triggers enforce append-only semantics.
--
-- NOTE: do not put a literal semicolon inside a comment line in any migration
-- file. The runner splits on bare semicolons and would treat the rest of the
-- comment as a new statement, producing a syntax error.

DROP TABLE IF EXISTS audit_log;

CREATE TABLE audit_log (
    audit_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type     TEXT    NOT NULL,
    actor          TEXT    NOT NULL,
    target         TEXT,
    timestamp      REAL    NOT NULL,
    details        TEXT    NOT NULL DEFAULT '{}',
    integrity_hash TEXT    NOT NULL DEFAULT ''
);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
    BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
    BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END
