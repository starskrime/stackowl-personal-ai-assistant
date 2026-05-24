"""AuditLogger — append-only audit log with SHA-256 chain integrity."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing-only
    pass

log = logging.getLogger("stackowl.audit")


class AuditLogger:
    """Append-only audit log backed by SQLite with chained SHA-256 integrity hashes."""

    def __init__(self, db_path: Path) -> None:
        # 1. ENTRY
        log.debug("[audit] logger.init: entry — db_path=%s", db_path)
        self._db_path = db_path
        # 4. EXIT
        log.debug("[audit] logger.init: exit")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(
        self,
        event_type: str,
        actor: str,
        target: str | None,
        details: dict[str, object],
    ) -> None:
        """Append one audit event and compute its integrity hash."""
        # 1. ENTRY
        log.debug(
            "[audit] logger.append: entry",
            extra={"_fields": {"event_type": event_type, "actor": actor, "target": target}},
        )
        timestamp = time.time()
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                # 2. DECISION — get previous hash for chain
                row = conn.execute(
                    "SELECT integrity_hash FROM audit_log ORDER BY audit_id DESC LIMIT 1"
                ).fetchone()
                prev_hash = row[0] if row else ""
                log.debug(
                    "[audit] logger.append: decision — chaining from prev_hash=%s",
                    (prev_hash[:8] + "...") if prev_hash else "(empty)",
                )
                # Compute integrity hash
                details_json = json.dumps(details, sort_keys=True)
                raw = prev_hash + event_type + str(timestamp) + details_json
                integrity_hash = hashlib.sha256(raw.encode()).hexdigest()

                # 3. STEP — INSERT
                log.debug("[audit] logger.append: step — inserting row")
                cursor = conn.execute(
                    """
                    INSERT INTO audit_log
                        (event_type, actor, target, timestamp, details, integrity_hash)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (event_type, actor, target, timestamp, details_json, integrity_hash),
                )
                conn.commit()
                audit_id = cursor.lastrowid
            finally:
                conn.close()
        except Exception as exc:
            log.error("[audit] logger.append: INSERT failed", exc_info=exc)
            raise

        # 4. EXIT
        log.debug(
            "[audit] logger.append: exit",
            extra={"_fields": {"audit_id": audit_id, "event_type": event_type}},
        )

    def tail(self, n: int = 50) -> list[dict[str, object]]:
        """Return the last *n* audit rows as dicts, oldest first."""
        # 1. ENTRY
        log.debug("[audit] logger.tail: entry", extra={"_fields": {"n": n}})
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                # 2. DECISION
                log.debug("[audit] logger.tail: decision — querying last %d rows", n)
                # 3. STEP
                rows = conn.execute(
                    """
                    SELECT * FROM (
                        SELECT * FROM audit_log ORDER BY audit_id DESC LIMIT ?
                    ) ORDER BY audit_id ASC
                    """,
                    (n,),
                ).fetchall()
                result = [dict(r) for r in rows]
            finally:
                conn.close()
        except Exception as exc:
            log.error("[audit] logger.tail: query failed", exc_info=exc)
            raise

        # 4. EXIT
        log.debug("[audit] logger.tail: exit", extra={"_fields": {"returned": len(result)}})
        return result

    def verify_chain(self) -> tuple[bool, int | None]:
        """Verify SHA-256 chain integrity of the entire audit log.

        Returns ``(True, None)`` if intact, or ``(False, broken_audit_id)``
        at the first row whose hash does not match the expected value.
        """
        # 1. ENTRY
        log.debug("[audit] logger.verify_chain: entry")
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                # 2. DECISION
                log.debug("[audit] logger.verify_chain: decision — reading all rows")
                # 3. STEP
                rows = conn.execute(
                    "SELECT * FROM audit_log ORDER BY audit_id ASC"
                ).fetchall()
            finally:
                conn.close()
        except Exception as exc:
            log.error("[audit] logger.verify_chain: query failed", exc_info=exc)
            raise

        prev_hash = ""
        for row in rows:
            d = dict(row)
            details_json = d["details"]
            raw = prev_hash + d["event_type"] + str(d["timestamp"]) + details_json
            expected = hashlib.sha256(raw.encode()).hexdigest()
            if d["integrity_hash"] != expected:
                log.warning(
                    "[audit] logger.verify_chain: chain broken",
                    extra={"_fields": {"audit_id": d["audit_id"]}},
                )
                return (False, d["audit_id"])
            prev_hash = d["integrity_hash"]

        # 4. EXIT
        log.debug(
            "[audit] logger.verify_chain: exit",
            extra={"_fields": {"rows_checked": len(rows), "intact": True}},
        )
        return (True, None)
