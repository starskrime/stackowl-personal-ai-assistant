"""PARL-2 (F076) — mean pairwise cosine is O(n·d), not O(n²·d).

The new sum-of-vectors identity over L2-normalized embeddings must produce the
SAME mean pairwise cosine as the legacy quadratic double-loop, to floating
tolerance, for arbitrary vector sets.
"""

from __future__ import annotations

import math
import random

import pytest

from stackowl.parliament.convergence import ConvergenceDetector


def _legacy_mean_pairwise(embeddings: list[list[float]]) -> float:
    """The original O(n²·d) reference implementation (pre-PARL-2)."""

    def cos(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)

    total = 0.0
    pairs = 0
    for i in range(len(embeddings)):
        for j in range(i + 1, len(embeddings)):
            total += cos(embeddings[i], embeddings[j])
            pairs += 1
    return total / pairs if pairs else 0.0


@pytest.mark.parametrize("n", [2, 3, 5, 8])
def test_linear_mean_equals_quadratic_reference(n: int) -> None:
    rng = random.Random(1234 + n)
    dim = 16
    embeddings = [[rng.uniform(-1.0, 1.0) for _ in range(dim)] for _ in range(n)]

    det = ConvergenceDetector()
    fast = det._mean_pairwise_cosine(embeddings)  # noqa: SLF001 — exercising the math
    slow = _legacy_mean_pairwise(embeddings)

    assert fast == pytest.approx(slow, abs=1e-9), f"n={n}: {fast} != {slow}"


def test_zero_vector_is_skipped_like_legacy() -> None:
    # A zero vector contributes 0.0 to each of its pairs in the legacy path; the
    # linear identity must treat it identically (it has zero norm → drops out).
    embeddings = [[1.0, 0.0], [0.0, 0.0], [0.0, 1.0]]
    det = ConvergenceDetector()
    fast = det._mean_pairwise_cosine(embeddings)  # noqa: SLF001
    slow = _legacy_mean_pairwise(embeddings)
    assert fast == pytest.approx(slow, abs=1e-9)


def test_identical_vectors_converge_to_one() -> None:
    embeddings = [[0.3, 0.4, 0.5]] * 4
    det = ConvergenceDetector()
    assert det._mean_pairwise_cosine(embeddings) == pytest.approx(1.0, abs=1e-9)  # noqa: SLF001
