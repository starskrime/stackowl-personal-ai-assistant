CREATE TABLE IF NOT EXISTS audit_log (
    audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type   TEXT    NOT NULL,
    actor        TEXT    NOT NULL,
    target       TEXT,
    timestamp    REAL    NOT NULL,
    details      TEXT    NOT NULL DEFAULT '{}',
    integrity_hash TEXT  NOT NULL DEFAULT ''
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
END;
