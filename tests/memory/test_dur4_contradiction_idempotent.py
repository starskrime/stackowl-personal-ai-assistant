"""DUR-4 / F068 — a crashed dream-worker run that re-scans the same
contradictions on resume must NOT write duplicate ``memory.contradiction``
audit rows.

The contradiction audit insert is made idempotent on a deterministic
contradiction id derived from the fact pair + explanation, so replaying the
phase (the crash-resume behaviour) is a no-op for the audit log.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.contradiction_detector import ContradictionReport
from stackowl.memory.dream_worker_helpers import mark_audit_contradictions

pytestmark = pytest.mark.asyncio


def _report(a: str, b: str, expl: str = "contradicts", conf: float = 0.9) -> ContradictionReport:
    return ContradictionReport(fact_id_a=a, fact_id_b=b, explanation=expl, confidence=conf)


async def _count_contradiction_rows(db: DbPool) -> int:
    rows = await db.fetch_all(
        "SELECT COUNT(*) AS n FROM audit_log WHERE event_type = 'memory.contradiction'"
    )
    return int(rows[0]["n"])


async def test_replay_does_not_duplicate_audit_rows(tmp_db: DbPool) -> None:
    """Calling mark_audit_contradictions twice with the same reports (the
    crash-resume re-scan) writes each contradiction exactly once."""
    reports = [_report("f1", "f2"), _report("f3", "f4")]

    await mark_audit_contradictions(tmp_db, reports)
    assert await _count_contradiction_rows(tmp_db) == 2  # noqa: PLR2004

    # Simulate a mid-phase crash + resume: the SAME reports are produced again.
    await mark_audit_contradictions(tmp_db, reports)
    assert await _count_contradiction_rows(tmp_db) == 2  # noqa: PLR2004 — no dupes


async def test_new_contradiction_after_resume_still_recorded(tmp_db: DbPool) -> None:
    """Idempotency is keyed per contradiction, not per call — a genuinely new
    contradiction on the resumed pass is still written."""
    await mark_audit_contradictions(tmp_db, [_report("f1", "f2")])
    await mark_audit_contradictions(
        tmp_db, [_report("f1", "f2"), _report("f5", "f6")]
    )
    assert await _count_contradiction_rows(tmp_db) == 2  # noqa: PLR2004


async def test_chain_stays_verifiable_after_dedup(tmp_db: DbPool) -> None:
    """The idempotent path must not void the audit hash chain."""
    from stackowl.audit.logger import AuditLogger

    await mark_audit_contradictions(tmp_db, [_report("f1", "f2")])
    await mark_audit_contradictions(tmp_db, [_report("f1", "f2")])  # replay
    logger = AuditLogger(db_path=tmp_db._path)
    ok, broken_at = logger.verify_chain()
    assert ok is True, f"chain broke at audit_id={broken_at}"
