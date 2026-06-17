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
    from stackowl.db.pool import DbPool

log = logging.getLogger("stackowl.audit")

# Lock-wait budget (ms) for a connection that loses the BEGIN IMMEDIATE race
# (F140). SQLite's spec default is 0 (fail instantly with "database is locked");
# some distro builds default to 5000 but that is NOT portable, and StackOwl must
# behave identically on every host. Set it explicitly on every connection so two
# concurrent appends serialize via the lock instead of one failing immediately.
_BUSY_TIMEOUT_MS = 5000

# Chain format version stamped on every new row. v1 rows (legacy / absent column)
# verify with the legacy formula; v2 rows fold actor+target+a version literal into
# a length-prefixed payload so who-did-what-to-whom is tamper-evident.
_CHAIN_VERSION = "v2"

# ASCII unit separator — joins length-prefixed fields so no field value can forge a
# boundary (the `actor="ab"|target=""` vs `actor="a"|target="b"` ambiguity).
_FIELD_SEP = "\x1f"


def _lp(value: str | None) -> str:
    """Length-prefix a (possibly None) field: ``None``/`""` -> ``0:`` (unambiguous)."""
    s = "" if value is None else value
    return f"{len(s)}:{s}"


def compute_integrity_hash(
    prev_hash: str,
    event_type: str,
    actor: str | None,
    target: str | None,
    timestamp: float,
    details_json: str,
) -> str:
    """Canonical v2 chain-hash chokepoint — THE single audit payload builder.

    Every audit writer (AuditLogger.append, scheduler write_audit, dream-worker,
    retention prune) MUST chain through this so the hash protects the SAME field
    set everywhere. Fields are length-prefixed and ``\\x1f``-joined; the version
    literal is folded in so a v1/v2 boundary never collides. ``None`` is canonical
    ``""`` and safe under length-prefixing (``0:`` differs from ``2:ab``).
    """
    payload = _FIELD_SEP.join(
        (
            _CHAIN_VERSION,
            _lp(prev_hash),
            _lp(event_type),
            _lp(actor),
            _lp(target),
            _lp(repr(timestamp)),
            _lp(details_json),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_integrity_hash_v1(
    prev_hash: str, event_type: str, timestamp: float, details_json: str
) -> str:
    """Reproduce the EXACT legacy v1 payload (``prev+type+str(ts)+details``).

    Used only to verify pre-migration rows so existing history stays verifiable.
    """
    raw = prev_hash + event_type + str(timestamp) + details_json
    return hashlib.sha256(raw.encode()).hexdigest()


async def chain_append_via_pool(
    db: DbPool,
    event_type: str,
    actor: str,
    target: str | None,
    timestamp: float,
    details_json: str,
) -> None:
    """Append a chained v2 audit row over a :class:`DbPool` (the shared chokepoint
    for the async writers: scheduler write_audit + dream-worker contradictions).

    Reads the prev_hash and INSERTs through the SAME single-serialized DbPool
    connection that every other write uses, so the prev_hash-read + INSERT are
    serialized relative to all other pool writes (R2). Computes the hash via the
    canonical :func:`compute_integrity_hash` so these rows chain identically to
    :meth:`AuditLogger.append` — closing the multi-writer break (R1) where these
    rows previously wrote integrity_hash='' and voided verify_chain.
    """
    # 1. ENTRY
    log.debug(
        "[audit] chain_append_via_pool: entry event_type=%s actor=%s", event_type, actor
    )
    rows = await db.fetch_all(
        "SELECT integrity_hash FROM audit_log ORDER BY audit_id DESC LIMIT 1"
    )
    prev_hash = rows[0]["integrity_hash"] if rows else ""
    integrity_hash = compute_integrity_hash(
        prev_hash, event_type, actor, target, timestamp, details_json
    )
    # 3. STEP — INSERT with chain_version stamped.
    await db.execute(
        "INSERT INTO audit_log "
        "(event_type, actor, target, timestamp, details, integrity_hash, chain_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_type, actor, target, timestamp, details_json, integrity_hash, _CHAIN_VERSION),
    )
    # 4. EXIT
    log.debug("[audit] chain_append_via_pool: exit event_type=%s", event_type)


class AuditLogger:
    """Append-only audit log backed by SQLite with chained SHA-256 integrity hashes."""

    def __init__(self, db_path: Path) -> None:
        # 1. ENTRY
        log.debug("[audit] logger.init: entry — db_path=%s", db_path)
        self._db_path = db_path
        # 4. EXIT
        log.debug("[audit] logger.init: exit")

    # ------------------------------------------------------------------
    # Schema (self-healing)
    # ------------------------------------------------------------------

    # Mirrors migration 0023/0027. Kept here so an AuditLogger constructed
    # against a DB that has not had the audit migration applied (e.g. a
    # caller-supplied path, or a DB created before 0023 landed) still writes
    # successfully instead of raising ``no such table: audit_log`` — the live
    # failure behind the swallowed "[consent] policy.request: audit append
    # failed" error. ``IF NOT EXISTS`` makes it idempotent and safe to run on
    # every open; the migration runner remains the canonical owner.
    _SCHEMA_DDL = (
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type     TEXT    NOT NULL,
            actor          TEXT    NOT NULL,
            target         TEXT,
            timestamp      REAL    NOT NULL,
            details        TEXT    NOT NULL DEFAULT '{}',
            integrity_hash TEXT    NOT NULL DEFAULT '',
            chain_version  TEXT    NOT NULL DEFAULT 'v1'
        )
        """,
        """
        CREATE TRIGGER IF NOT EXISTS audit_log_no_update
            BEFORE UPDATE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
            BEFORE DELETE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only');
        END
        """,
    )

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Idempotently provision the audit_log table + append-only triggers.

        Also self-heals the ``chain_version`` column on a caller-supplied DB that
        predates migration 0059 (mirrors the additive-migration pattern). The
        guarded ADD COLUMN logs the already-applied branch — never silent.
        """
        for stmt in self._SCHEMA_DDL:
            conn.execute(stmt)
        # Self-heal: add chain_version to a pre-existing table that lacks it.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(audit_log)")}
        if "chain_version" not in cols:
            try:
                conn.execute(
                    "ALTER TABLE audit_log "
                    "ADD COLUMN chain_version TEXT NOT NULL DEFAULT 'v1'"
                )
                log.info("[audit] logger._ensure_schema: added chain_version column")
            except sqlite3.OperationalError as exc:
                # Concurrent/already-applied — additive and idempotent; log loud.
                log.info(
                    "[audit] logger._ensure_schema: chain_version add no-op (already applied): %s",
                    exc,
                )

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
            conn = sqlite3.connect(self._db_path, isolation_level=None)
            try:
                # F140 — explicit lock-wait so a concurrent writer that loses the
                # BEGIN IMMEDIATE race WAITS instead of failing instantly. Portable
                # across SQLite builds (spec default is 0).
                conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
                self._ensure_schema(conn)
                details_json = json.dumps(details, sort_keys=True)
                # Serialize the prev_hash-read + INSERT inside one write txn so two
                # concurrent appends cannot chain off the same prev_hash (R2). SQLite
                # BEGIN IMMEDIATE takes the write lock up-front; cross-platform.
                conn.execute("BEGIN IMMEDIATE")
                try:
                    # 2. DECISION — read previous hash for the chain (under the lock)
                    row = conn.execute(
                        "SELECT integrity_hash FROM audit_log "
                        "ORDER BY audit_id DESC LIMIT 1"
                    ).fetchone()
                    prev_hash = row[0] if row else ""
                    log.debug(
                        "[audit] logger.append: decision — chaining from prev_hash=%s",
                        (prev_hash[:8] + "...") if prev_hash else "(empty)",
                    )
                    integrity_hash = compute_integrity_hash(
                        prev_hash, event_type, actor, target, timestamp, details_json
                    )
                    # 3. STEP — INSERT (chain_version stamped so verify is version-aware)
                    log.debug("[audit] logger.append: step — inserting row")
                    cursor = conn.execute(
                        """
                        INSERT INTO audit_log
                            (event_type, actor, target, timestamp, details,
                             integrity_hash, chain_version)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_type,
                            actor,
                            target,
                            timestamp,
                            details_json,
                            integrity_hash,
                            _CHAIN_VERSION,
                        ),
                    )
                    conn.execute("COMMIT")
                    audit_id = cursor.lastrowid
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
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
                conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
                self._ensure_schema(conn)
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
                conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
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
            # Per-row version branch: legacy rows (v1 / absent column) verify with
            # the legacy formula; v2 rows fold actor+target. prev_hash chains across
            # the boundary unchanged so existing history stays verifiable.
            version = d.get("chain_version") or "v1"
            if version == _CHAIN_VERSION:
                expected = compute_integrity_hash(
                    prev_hash,
                    d["event_type"],
                    d["actor"],
                    d["target"],
                    d["timestamp"],
                    details_json,
                )
            else:
                expected = compute_integrity_hash_v1(
                    prev_hash, d["event_type"], d["timestamp"], details_json
                )
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
