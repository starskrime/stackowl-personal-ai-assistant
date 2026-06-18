"""TDD T20 — mark_audit_contradictions binds no audit_id + float ts.

Regression: before the fix, _INSERT_AUDIT_SQL supplied a UUID string for
audit_id INTEGER PRIMARY KEY AUTOINCREMENT → sqlite3.IntegrityError swallowed
as a WARNING → audit row was silently lost (len == 0).

After the fix: 5-col INSERT omitting audit_id lets AUTOINCREMENT assign an
integer PK; timestamp is bound as time.time() (float). Row lands, types match.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.contradiction_detector import ContradictionReport
from stackowl.memory.dream_worker_helpers import mark_audit_contradictions

pytestmark = pytest.mark.asyncio


async def test_mark_audit_contradictions_row_lands(tmp_db: DbPool) -> None:
    """A contradiction report MUST persist a row in audit_log (no datatype mismatch)."""
    report = ContradictionReport(
        fact_id_a="fact-aaa",
        fact_id_b="fact-bbb",
        explanation="test contradiction",
        confidence=0.9,
    )

    await mark_audit_contradictions(tmp_db, reports=[report])

    rows = await tmp_db.fetch_all("SELECT audit_id, timestamp, event_type FROM audit_log")
    assert len(rows) == 1, (
        "Expected exactly 1 audit row — 0 means the INSERT silently failed "
        "(likely datatype mismatch with UUID string bound to INTEGER PK)"
    )
    row = rows[0]
    assert isinstance(row["audit_id"], int), (
        f"audit_id must be an AUTOINCREMENT integer, got {type(row['audit_id'])!r}: {row['audit_id']!r}"
    )
    assert isinstance(row["timestamp"], float), (
        f"timestamp must be a REAL float (time.time()), got {type(row['timestamp'])!r}: {row['timestamp']!r}"
    )
    assert row["event_type"] == "memory.contradiction"


async def test_mark_audit_contradictions_multiple_reports(tmp_db: DbPool) -> None:
    """Multiple reports each produce their own audit row."""
    reports = [
        ContradictionReport(
            fact_id_a=f"fact-a{i}",
            fact_id_b=f"fact-b{i}",
            explanation=f"contradiction {i}",
            confidence=0.8,
        )
        for i in range(3)
    ]

    await mark_audit_contradictions(tmp_db, reports=reports)

    rows = await tmp_db.fetch_all("SELECT audit_id FROM audit_log")
    assert len(rows) == 3, f"Expected 3 rows, got {len(rows)}"
    # All PKs must be distinct integers
    pks = [r["audit_id"] for r in rows]
    assert len(set(pks)) == 3
    assert all(isinstance(pk, int) for pk in pks)


async def test_mark_audit_contradictions_empty_is_noop(tmp_db: DbPool) -> None:
    """Empty reports list must not touch audit_log."""
    await mark_audit_contradictions(tmp_db, reports=[])
    rows = await tmp_db.fetch_all("SELECT audit_id FROM audit_log")
    assert len(rows) == 0
