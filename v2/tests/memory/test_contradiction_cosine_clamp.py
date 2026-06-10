"""Regression: ``_cosine`` must clamp float overshoot to the true range [-1, 1].

Production logs showed ``contradiction_detector.detect: scan failed — returning []``
caused by ``_cosine`` returning ``1.0000000000000002`` (a floating-point artifact),
which fed ``confidence=sim`` into ``ContradictionReport`` whose field is
``Field(le=1.0)`` → ValidationError → the *entire* contradiction/near-duplicate
scan aborted for the batch (DreamWorker degraded).

These tests pin the mathematical invariant of ``_cosine`` and drive the real bug
end-to-end through ``ContradictionDetector.detect``.
"""

from __future__ import annotations

import pytest

from stackowl.memory.contradiction_detector import ContradictionDetector, _cosine
from stackowl.memory.models import StagedFact


def test_cosine_self_never_exceeds_one() -> None:
    """Invariant: cosine of a vector with itself is mathematically 1.0, never > 1.0.

    On the unfixed code, multi-dim float vectors can compute to 1.0000000000000002.
    """
    vectors: list[list[float]] = [
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
        [float(i) * 0.0173 + 0.0001 for i in range(384)],
        [0.0007, 0.9991, 0.3333, 0.6666, 0.1234, 0.5678, 0.4321, 0.8765],
    ]
    for v in vectors:
        sim = _cosine(v, v)
        assert sim <= 1.0, f"_cosine(v, v) overshot: {sim!r}"


def test_cosine_opposite_never_below_minus_one() -> None:
    """Invariant: cosine of opposite vectors is -1.0, never < -1.0."""
    v = [float(i) * 0.0173 + 0.0001 for i in range(384)]
    neg = [-x for x in v]
    sim = _cosine(v, neg)
    assert sim >= -1.0, f"_cosine(v, -v) undershot: {sim!r}"


def test_cosine_normal_values_unchanged() -> None:
    """The clamp must not perturb in-range values."""
    # Orthogonal → ~0.0
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0, abs=1e-9)
    # Identical → ~1.0
    assert _cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0, abs=1e-9)
    # Known pair: [1,0] vs [1,1] → 1/sqrt(2)
    assert _cosine([1.0, 0.0], [1.0, 1.0]) == pytest.approx(0.70710678, abs=1e-6)
    # Zero-magnitude guard preserved
    assert _cosine([0.0, 0.0], [1.0, 2.0]) == 0.0


def _staged(fact_id: str, embedding: list[float]) -> StagedFact:
    return StagedFact(
        fact_id=fact_id,
        content="x",
        source_type="conversation",
        source_ref="ref",
        confidence=0.5,
        embedding=embedding,
    )


async def test_detect_survives_identical_embeddings_end_to_end() -> None:
    """End-to-end: two facts with identical embeddings + same source_type must yield
    a near-duplicate report with a valid (in-bounds) confidence — not an empty list.

    On the unfixed code, if ``_cosine`` overshoots to >1.0, ``ContradictionReport``
    raises ValidationError and ``detect()`` swallows it, returning ``[]``.
    """
    emb = [float(i) * 0.0173 + 0.0001 for i in range(384)]
    f1 = _staged("f1", emb)
    f2 = _staged("f2", list(emb))
    reports = await ContradictionDetector().detect([f1, f2])
    assert reports, "detect() returned [] — cosine overshoot aborted the scan"
    assert len(reports) == 1
    assert 0.0 <= reports[0].confidence <= 1.0
    assert reports[0].explanation == "near-duplicate"
