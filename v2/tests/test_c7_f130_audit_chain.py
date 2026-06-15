"""C7 / F130 — audit hash chain must cover actor+target and survive all writers.

Merge-gates (assert OUTCOMES, not hash bytes):
* Tampering with `actor` OR `target` (bypassing append-only triggers) breaks the
  chain — verify_chain() == (False, id). Currently passes the tamper undetected.
* Multi-writer regression: after a scheduler write_audit / dream-worker /
  retention-prune row, verify_chain() == (True, None). Currently False.
* Concurrency: two concurrent append()s leave the chain intact (no shared
  prev_hash).
* Migration-safety: a legacy v1 row + a new v2 row verify intact end-to-end.
* Field-shuffle: (actor,target) swap produces different hashes (length-prefix).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path

import pytest

from stackowl.audit.logger import (
    AuditLogger,
    compute_integrity_hash,
    compute_integrity_hash_v1,
)


@pytest.fixture()
def notrigger_db_path(tmp_path: Path) -> Path:
    """audit_log WITHOUT triggers (so tests can tamper) and WITHOUT chain_version
    (so the self-heal ADD COLUMN path is exercised)."""
    p = tmp_path / "audit_notrigger.db"
    conn = sqlite3.connect(p)
    conn.execute(
        """
        CREATE TABLE audit_log (
            audit_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type     TEXT    NOT NULL,
            actor          TEXT    NOT NULL,
            target         TEXT,
            timestamp      REAL    NOT NULL,
            details        TEXT    NOT NULL DEFAULT '{}',
            integrity_hash TEXT    NOT NULL DEFAULT ''
        )
        """
    )
    conn.commit()
    conn.close()
    return p


class TestActorTargetTamper:
    def test_actor_tamper_breaks_chain(self, notrigger_db_path: Path) -> None:
        logger = AuditLogger(notrigger_db_path)
        logger.append("event.a", "alice", "resource-1", {"k": "v"})
        logger.append("event.b", "bob", "resource-2", {"k": "v2"})
        ok, _ = logger.verify_chain()
        assert ok is True
        # Tamper actor on row 1, bypassing the triggers (this fixture has none).
        conn = sqlite3.connect(notrigger_db_path)
        # Drop the append-only triggers (the self-heal schema added them) so this
        # test can simulate an attacker with raw DB write access.
        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
        conn.execute("UPDATE audit_log SET actor = 'mallory' WHERE audit_id = 1")
        conn.commit()
        conn.close()
        ok, broken = logger.verify_chain()
        assert ok is False, "actor tamper went undetected — actor not in hashed payload"
        assert broken == 1

    def test_target_tamper_breaks_chain(self, notrigger_db_path: Path) -> None:
        logger = AuditLogger(notrigger_db_path)
        logger.append("event.a", "alice", "resource-1", {"k": "v"})
        conn = sqlite3.connect(notrigger_db_path)
        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
        conn.execute("UPDATE audit_log SET target = 'evil-resource' WHERE audit_id = 1")
        conn.commit()
        conn.close()
        ok, broken = logger.verify_chain()
        assert ok is False, "target tamper went undetected"
        assert broken == 1


class TestFieldShuffle:
    def test_actor_target_swap_differs(self) -> None:
        h1 = compute_integrity_hash("", "e", "ab", "", 1.0, "{}")
        h2 = compute_integrity_hash("", "e", "a", "b", 1.0, "{}")
        assert h1 != h2, "length-prefix must disambiguate actor/target boundary"


class TestMigrationSafety:
    def test_v1_then_v2_rows_verify(self, notrigger_db_path: Path) -> None:
        # Hand-write a legacy v1 row (no chain_version, legacy formula).
        ts = time.time()
        details = json.dumps({"legacy": True}, sort_keys=True)
        v1_hash = compute_integrity_hash_v1("", "legacy.event", ts, details)
        conn = sqlite3.connect(notrigger_db_path)
        conn.execute(
            "INSERT INTO audit_log (event_type, actor, target, timestamp, details, "
            "integrity_hash) VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy.event", "old", None, ts, details, v1_hash),
        )
        conn.commit()
        conn.close()
        # Now append a v2 row via the logger; chain must verify end-to-end.
        logger = AuditLogger(notrigger_db_path)
        logger.append("new.event", "alice", "res", {"new": True})
        ok, broken = logger.verify_chain()
        assert ok is True, f"v1+v2 chain broke at {broken}"


class TestConcurrency:
    async def test_concurrent_appends_chain_intact(self, notrigger_db_path: Path) -> None:
        logger = AuditLogger(notrigger_db_path)

        async def _one(i: int) -> None:
            await asyncio.to_thread(
                logger.append, "concurrent.event", f"actor-{i}", None, {"i": i}
            )

        await asyncio.gather(*[_one(i) for i in range(8)])
        ok, broken = logger.verify_chain()
        assert ok is True, f"concurrent appends broke chain at {broken}"
        # No two rows share a prev-derived hash — verify_chain proves linkage.
        assert len(logger.tail(100)) == 8
