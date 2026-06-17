"""CONC-5 (F140) — concurrent AuditLogger.append() must yield a single unbroken
chain, never fork and never raise 'database is locked'.

append() opens its OWN sqlite connection per call and does read-prev-hash + INSERT
inside a ``BEGIN IMMEDIATE`` write txn. With multiple connections hitting the same
file concurrently, the writer that loses the race needs ``PRAGMA busy_timeout`` to
wait for the lock instead of failing instantly. The threads + barrier below force
real overlap; we then assert verify_chain() is intact and every event landed.
"""

from __future__ import annotations

import threading
from pathlib import Path

from stackowl.audit.logger import AuditLogger


def test_concurrent_appends_yield_one_unbroken_chain(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    # Provision the schema once up-front so all writers share an existing table.
    AuditLogger(db_path).append("seed", "system", None, {"n": -1})

    n_threads = 8
    per_thread = 10
    start = threading.Barrier(n_threads)
    errors: list[BaseException] = []

    def writer(wid: int) -> None:
        logger = AuditLogger(db_path)  # own connection per call inside append()
        start.wait()
        try:
            for i in range(per_thread):
                logger.append("event", f"actor{wid}", None, {"wid": wid, "i": i})
        except Exception as exc:  # noqa: BLE001 — capture for the assert (no 'locked')
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(w,)) for w in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"concurrent append raised (likely 'database is locked'): {errors!r}"

    logger = AuditLogger(db_path)
    ok, broken = logger.verify_chain()
    assert ok, f"chain forked under concurrent append at audit_id={broken}"

    # Every event landed: seed + (n_threads * per_thread).
    rows = logger.tail(n_threads * per_thread + 10)
    assert len(rows) == 1 + n_threads * per_thread


def test_append_sets_explicit_busy_timeout() -> None:
    """append/tail/verify_chain must set PRAGMA busy_timeout explicitly (F140), so
    a lock-loser WAITS instead of failing instantly — portable across SQLite
    builds whose default is 0 vs 5000. Source-scan: relying on the build default
    is exactly the non-portable hazard the story closes."""
    import inspect

    from stackowl.audit import logger as logger_mod

    for method in (
        logger_mod.AuditLogger.append,
        logger_mod.AuditLogger.tail,
        logger_mod.AuditLogger.verify_chain,
    ):
        src = inspect.getsource(method)
        assert "busy_timeout" in src, (
            f"{method.__name__} must set PRAGMA busy_timeout explicitly (F140)"
        )
