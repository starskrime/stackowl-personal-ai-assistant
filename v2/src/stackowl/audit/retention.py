"""AuditRetention — prunes old audit_log rows in DreamWorker context."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger("stackowl.audit")

_TRIGGER_SQL = """\
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
    BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END\
"""


class AuditRetention:
    """Prunes audit_log rows older than *retention_days*.

    Because the ``audit_log_no_delete`` trigger blocks all DELETEs, the prune
    method temporarily drops the trigger inside a transaction, performs the
    DELETE, and re-creates the trigger atomically before committing.  This
    privileged operation is documented in the governance spec and only runs
    in the DreamWorker scheduled context.
    """

    def __init__(self, db_path: Path, retention_days: int = 90) -> None:
        # 1. ENTRY
        log.debug(
            "[audit] retention.init: entry",
            extra={"_fields": {"db_path": str(db_path), "retention_days": retention_days}},
        )
        self._db_path = db_path
        self._retention_days = retention_days
        # 4. EXIT
        log.debug("[audit] retention.init: exit")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prune(self) -> int:
        """Delete audit rows older than *retention_days* and append a prune record.

        Returns the number of rows deleted.
        """
        # 1. ENTRY
        log.debug(
            "[audit] retention.prune: entry",
            extra={"_fields": {"retention_days": self._retention_days}},
        )

        # 2. DECISION — compute cutoff timestamp
        cutoff_dt = datetime.now(UTC) - timedelta(days=self._retention_days)
        cutoff_ts = cutoff_dt.timestamp()
        log.debug(
            "[audit] retention.prune: decision — cutoff computed",
            extra={"_fields": {"cutoff_iso": cutoff_dt.isoformat(), "cutoff_ts": cutoff_ts}},
        )

        try:
            conn = sqlite3.connect(self._db_path)
            conn.isolation_level = None  # manual transaction control
            try:
                # 3. STEP — count rows to prune
                row = conn.execute(
                    "SELECT COUNT(*) FROM audit_log WHERE timestamp < ?",
                    (cutoff_ts,),
                ).fetchone()
                count_to_prune: int = row[0] if row else 0
                log.debug(
                    "[audit] retention.prune: step — row count",
                    extra={"_fields": {"to_prune": count_to_prune}},
                )

                if count_to_prune > 0:
                    # Temporarily lift the no-delete trigger, delete old rows,
                    # then re-create the trigger in one atomic transaction.
                    conn.execute("BEGIN EXCLUSIVE")
                    try:
                        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
                        conn.execute(
                            "DELETE FROM audit_log WHERE timestamp < ?",
                            (cutoff_ts,),
                        )
                        conn.execute(_TRIGGER_SQL)
                        conn.execute("COMMIT")
                        log.debug(
                            "[audit] retention.prune: step — deleted old rows and re-created trigger",
                            extra={"_fields": {"deleted": count_to_prune}},
                        )
                    except Exception:
                        try:
                            conn.execute("ROLLBACK")
                        except Exception as rb_exc:
                            log.error(
                                "[audit] retention.prune: rollback failed",
                                exc_info=rb_exc,
                            )
                        raise

                # Find oldest kept row for details
                oldest_kept: float | None = None
                oldest_row = conn.execute(
                    "SELECT MIN(timestamp) FROM audit_log"
                ).fetchone()
                if oldest_row and oldest_row[0] is not None:
                    oldest_kept = float(oldest_row[0])

                # Append prune audit record
                self._append_prune_record(
                    conn,
                    pruned_count=count_to_prune,
                    oldest_kept_at=oldest_kept,
                )
            finally:
                conn.close()

        except Exception as exc:
            log.error("[audit] retention.prune: failed", exc_info=exc)
            raise

        # 4. EXIT
        log.debug(
            "[audit] retention.prune: exit",
            extra={"_fields": {"pruned": count_to_prune}},
        )
        return count_to_prune

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _append_prune_record(
        self,
        conn: sqlite3.Connection,
        pruned_count: int,
        oldest_kept_at: float | None,
    ) -> None:
        """Append a system_audit_prune row to the audit log."""
        log.debug(
            "[audit] retention._append_prune_record: entry",
            extra={"_fields": {"pruned_count": pruned_count}},
        )
        details = json.dumps(
            {
                "pruned_count": pruned_count,
                "oldest_kept_at": oldest_kept_at,
                "retention_days": self._retention_days,
            }
        )
        ts = time.time()
        conn.execute(
            """
            INSERT INTO audit_log
                (event_type, actor, target, timestamp, details, integrity_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("system_audit_prune", "system", None, ts, details, ""),
        )
        if conn.isolation_level is not None:
            # If autocommit mode is active we need explicit commit
            conn.commit()
        log.debug("[audit] retention._append_prune_record: exit")
