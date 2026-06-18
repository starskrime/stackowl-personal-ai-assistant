"""C7 / F130b — all audit writers must chain (the highest-value real-world gate).

3 of 4 audit_log writers (scheduler write_audit, dream-worker contradiction
marker, retention prune) previously wrote integrity_hash='' and no chain_version,
so verify_chain() returned (False, id) on any prod DB that had run a scheduled
job / dream pass / retention prune. These tests assert that after each such
write, verify_chain() == (True, None).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stackowl.audit.logger import AuditLogger


class TestSchedulerWriteAuditChains:
    async def test_scheduler_write_audit_chains(self, tmp_db: Any) -> None:
        from stackowl.scheduler.scheduler_helpers import write_audit

        await write_audit(tmp_db, "job_paused", "job-1")
        await write_audit(tmp_db, "job_resumed", "job-1", details={"next": "x"})
        logger = AuditLogger(tmp_db._path)
        ok, broken = logger.verify_chain()
        assert ok is True, f"scheduler write_audit voided the chain at {broken}"


class TestDreamWorkerChains:
    async def test_dream_worker_contradiction_chains(self, tmp_db: Any) -> None:
        from stackowl.memory.contradiction_detector import ContradictionReport
        from stackowl.memory.dream_worker_helpers import mark_audit_contradictions

        reports = [
            ContradictionReport(
                fact_id_a="a1",
                fact_id_b="b1",
                explanation="conflict",
                confidence=0.9,
            )
        ]
        await mark_audit_contradictions(tmp_db, reports)
        logger = AuditLogger(tmp_db._path)
        ok, broken = logger.verify_chain()
        assert ok is True, f"dream-worker contradiction voided the chain at {broken}"


class TestRetentionPruneChains:
    def test_retention_prune_record_chains(self, tmp_path: Path) -> None:
        import sqlite3

        from stackowl.audit.retention import AuditRetention

        db_path = tmp_path / "ret.db"
        # Seed a real chained row via AuditLogger so prune chains off it.
        logger = AuditLogger(db_path)
        logger.append("seed.event", "system", None, {"seed": True})
        # Insert an OLD row to be pruned (timestamp well in the past).
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
        conn.execute(
            "INSERT INTO audit_log (event_type, actor, target, timestamp, details, "
            "integrity_hash, chain_version) VALUES ('old', 'system', NULL, 1.0, '{}', '', 'v1')"
        )
        conn.commit()
        conn.close()

        retention = AuditRetention(db_path=db_path, retention_days=1)
        retention.prune()
        # After pruning + appending the prune record, the chain from the remaining
        # rows must verify (the prune record chained off the surviving tail).
        ok, broken = logger.verify_chain()
        assert ok is True, f"retention prune record voided the chain at {broken}"
