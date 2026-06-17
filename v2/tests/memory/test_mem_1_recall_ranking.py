"""MEM-1 (F073) — recall blends relevance × recency-decay × reinforcement × trust.

A single blended rank is applied before truncation. A freshly-reinforced
preference must outrank a year-old one-off even when the stale fact arrived
slightly higher in raw relevance order. N and the decay half-life are
config-driven (MemorySettings).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from stackowl.memory.models import MemoryRecord
from stackowl.memory.recall_ranker import RecallRanker


def _record(
    fact_id: str,
    *,
    days_old: float,
    reinforcement: int,
    trust: str = "trusted",
) -> MemoryRecord:
    return MemoryRecord(
        fact_id=fact_id,
        content=f"content-{fact_id}",
        embedding=[0.1, 0.2, 0.3],
        embedding_model="m",
        committed_at=datetime.now(UTC) - timedelta(days=days_old),
        source_type="conversation_fact",
        source_ref="s",
        reinforcement_count=reinforcement,
        trust=trust,  # type: ignore[arg-type]
    )


def test_fresh_reinforced_outranks_year_old_oneoff() -> None:
    ranker = RecallRanker(decay_half_life_days=30.0)
    # Stale arrives FIRST in raw relevance order (index 0 = most relevant).
    stale = _record("stale", days_old=365, reinforcement=0)
    fresh = _record("fresh", days_old=1, reinforcement=5)
    ranked = ranker.rank([stale, fresh], limit=2)
    assert [r.fact_id for r in ranked] == ["fresh", "stale"]


def test_truncates_to_limit() -> None:
    ranker = RecallRanker(decay_half_life_days=30.0)
    records = [_record(f"f{i}", days_old=i, reinforcement=0) for i in range(10)]
    ranked = ranker.rank(records, limit=3)
    assert len(ranked) == 3


def test_trust_breaks_ties_toward_trusted() -> None:
    ranker = RecallRanker(decay_half_life_days=30.0)
    # Same age + reinforcement + relevance position; trust differs.
    untrusted = _record("untrusted", days_old=5, reinforcement=1, trust="untrusted")
    trusted = _record("trusted", days_old=5, reinforcement=1, trust="trusted")
    ranked = ranker.rank([untrusted, trusted], limit=2)
    assert ranked[0].fact_id == "trusted"


def test_recency_decay_demotes_old_fact() -> None:
    ranker = RecallRanker(decay_half_life_days=10.0)
    old = _record("old", days_old=100, reinforcement=0)
    new = _record("new", days_old=0, reinforcement=0)
    # Equal reinforcement/trust; the newer fact wins purely on recency decay,
    # despite the old one being first in raw relevance order.
    ranked = ranker.rank([old, new], limit=2)
    assert ranked[0].fact_id == "new"


def test_empty_input_returns_empty() -> None:
    ranker = RecallRanker(decay_half_life_days=30.0)
    assert ranker.rank([], limit=5) == []
