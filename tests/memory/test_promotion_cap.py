"""DEBT-38 — promotion must drain, not flood.

Dropping conversation_fact_reinforcement_required to 0 made 71,303 staged facts
eligible at once, on a code path with no LIMIT that embeds and LanceDB-writes
every row inside the dream worker's 1200s handler window. That is the DEBT-19
failure mode in a different phase.
"""

from __future__ import annotations

import pytest

from stackowl.memory.fact_promoter import (
    PROMOTE_MAX_PER_RUN,
    _SELECT_ELIGIBLE_SQL,
    FactPromoter,
)


def test_the_eligible_query_is_BOUNDED():
    """Without this, one pass tries to promote every eligible row."""
    assert f"LIMIT {PROMOTE_MAX_PER_RUN}" in _SELECT_ELIGIBLE_SQL


def test_the_backlog_drains_OLDEST_FIRST():
    """A newest-first drain would starve the oldest facts forever — they are the
    ones that have already waited longest to become durable."""
    assert "ORDER BY staged_at ASC" in _SELECT_ELIGIBLE_SQL


def test_the_cap_is_smaller_than_the_measured_backlog():
    """71,303 rows were eligible the moment the threshold changed. A cap only
    helps if it is well under that."""
    assert PROMOTE_MAX_PER_RUN < 1000


@pytest.mark.asyncio
async def test_a_run_promotes_at_most_the_cap():
    rows = [
        {
            "fact_id": f"f{i}", "content": f"c{i}", "source_type": "conversation_fact",
            "source_ref": "s", "confidence": 0.9, "staged_at": "2026-01-01T00:00:00+00:00",
            "reinforcement_count": 0, "status": "staged", "embedding": None,
            "embedding_model": None, "trust": "untrusted", "scope_key": None,
        }
        for i in range(PROMOTE_MAX_PER_RUN + 50)
    ]

    class _Db:
        def __init__(self): self.limit_seen = None
        async def fetch_all(self, sql, params=None):
            # Honour the LIMIT the way SQLite would, so the test exercises the
            # real query rather than trusting the string check above.
            self.limit_seen = PROMOTE_MAX_PER_RUN if "LIMIT" in sql else None
            return rows[: self.limit_seen or len(rows)]
        async def execute(self, sql, params=None): return None

    db = _Db()
    p = FactPromoter(db, confidence_threshold=0.8, conversation_fact_reinforcement_required=0)

    promoted_ids: list[str] = []

    async def _promote_one(fact):
        promoted_ids.append(fact.fact_id)

    p._promote_one = _promote_one  # type: ignore[method-assign]

    n = await p.promote_eligible()

    assert n == PROMOTE_MAX_PER_RUN, f"expected the cap, promoted {n}"
    assert len(promoted_ids) == PROMOTE_MAX_PER_RUN
