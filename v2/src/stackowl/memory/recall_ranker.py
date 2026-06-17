"""RecallRanker — blended recall ordering (MEM-1 / F073).

``retrieve()`` previously returned the raw ANN/BM25 relevance order and truncated
to a fixed top-5: pure relevance, no freshness or reinforcement. A stale one-off
that embedded slightly closer could crowd out a freshly-reinforced preference.

This ranker blends FOUR axes into a single score, applied BEFORE truncation:

    score = relevance × recency_decay × reinforcement_boost × trust_weight

* **relevance** — the candidates arrive in relevance order (ANN cosine / BM25),
  but carry no scalar score. We derive a monotonically-decreasing weight from the
  candidate's rank position so the recall engine's ordering is honoured as the
  dominant signal, while the other axes can still overtake a near-tie.
* **recency_decay** — ``2 ** (-age_days / half_life_days)``; half-life is
  config-driven (``MemorySettings.recall_decay_half_life_days``).
* **reinforcement_boost** — saturating ``1 + k·ln(1 + reinforcement_count)`` so a
  repeatedly-confirmed fact is lifted without unbounded domination.
* **trust_weight** — trusted > self > untrusted, a gentle multiplier that breaks
  ties toward higher-provenance facts (never enough to resurrect a far-stale,
  irrelevant fact on trust alone).

Pure + deterministic + side-effect-free so it is unit-testable in isolation and
the policy lives in ONE place.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from stackowl.infra.observability import log
from stackowl.memory.models import MemoryRecord

# Trust multipliers — provenance is a tie-breaker, not a relevance override.
_TRUST_WEIGHT: dict[str, float] = {
    "trusted": 1.0,
    "self": 0.9,
    "untrusted": 0.75,
}
_DEFAULT_TRUST_WEIGHT = 0.75

# Reinforcement boost coefficient — ln(1+n) is saturating, so k scales how much a
# repeatedly-confirmed preference is lifted. k=0.5 ⇒ +~0.35 at n=1, +~1.0 at n=6.
_REINFORCEMENT_K = 0.5

# Rank-decay base for the relevance weight: weight = base ** position. Closer to
# 1.0 = flatter (the other axes matter more); lower = relevance dominates harder.
_RANK_DECAY_BASE = 0.85


class RecallRanker:
    """Blends relevance order, recency, reinforcement, and trust into one rank."""

    def __init__(
        self,
        decay_half_life_days: float = 30.0,
        reinforcement_k: float = _REINFORCEMENT_K,
        rank_decay_base: float = _RANK_DECAY_BASE,
    ) -> None:
        # A non-positive half-life would make the decay undefined; clamp to a
        # tiny positive so recency still strictly decreases with age.
        self._half_life = max(1e-6, decay_half_life_days)
        self._reinforcement_k = max(0.0, reinforcement_k)
        self._rank_decay_base = min(0.999999, max(1e-6, rank_decay_base))

    def rank(
        self,
        records: list[MemoryRecord],
        limit: int,
        *,
        now: datetime | None = None,
    ) -> list[MemoryRecord]:
        """Return ``records`` re-ordered by blended score, truncated to ``limit``.

        ``records`` must arrive in the recall engine's relevance order (index 0 =
        most relevant). ``now`` is injectable for deterministic tests.
        """
        log.memory.debug(
            "[memory] recall_ranker.rank: entry",
            extra={"_fields": {"candidates": len(records), "limit": limit}},
        )
        if not records:
            return []
        ref_now = now or datetime.now(UTC)
        scored: list[tuple[float, int, MemoryRecord]] = []
        for position, record in enumerate(records):
            relevance = self._rank_decay_base**position
            recency = self._recency_decay(record.committed_at, ref_now)
            reinforcement = 1.0 + self._reinforcement_k * math.log1p(
                max(0, record.reinforcement_count)
            )
            trust = _TRUST_WEIGHT.get(record.trust, _DEFAULT_TRUST_WEIGHT)
            score = relevance * recency * reinforcement * trust
            # position is the stable secondary key so equal scores preserve the
            # original relevance order (deterministic, never a flapping sort).
            scored.append((score, position, record))
        scored.sort(key=lambda t: (-t[0], t[1]))
        ranked = [record for _score, _pos, record in scored[: max(0, limit)]]
        log.memory.debug(
            "[memory] recall_ranker.rank: exit",
            extra={
                "_fields": {
                    "returned": len(ranked),
                    "top_fact_id": ranked[0].fact_id if ranked else None,
                }
            },
        )
        return ranked

    def _recency_decay(self, committed_at: datetime, now: datetime) -> float:
        """``2 ** (-age_days / half_life)`` — halves every ``half_life`` days."""
        committed = committed_at
        if committed.tzinfo is None:
            committed = committed.replace(tzinfo=UTC)
        age_days = (now - committed).total_seconds() / 86400.0
        if age_days <= 0.0:
            return 1.0  # future/now timestamps never get a freshness penalty
        return float(2.0 ** (-age_days / self._half_life))
