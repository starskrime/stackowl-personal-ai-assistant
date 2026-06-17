"""ConvergenceDetector — cosine-similarity-based agreement detection."""

from __future__ import annotations

import math
import time

from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.infra.observability import log
from stackowl.parliament.models import ParliamentRound


class ConvergenceDetector:
    """Detects when Parliament owls have reached sufficient agreement.

    Computes mean pairwise cosine similarity over embeddings of the round's
    responses. If no ``EmbeddingRegistry`` is provided, gracefully degrades
    to "never converge" (logs a one-time warning per process so we don't
    silently disable the optimisation).
    """

    def __init__(
        self,
        threshold: float = 0.85,
        embedding_registry: EmbeddingRegistry | None = None,
    ) -> None:
        self._threshold = threshold
        self._embedding_registry = embedding_registry
        self._degraded_warned = False

    async def check(self, round_: ParliamentRound) -> bool:
        """Return True if the round's responses show ``mean_sim >= threshold``."""
        log.parliament.debug(
            "[parliament] convergence.check: entry",
            extra={
                "_fields": {
                    "round_number": round_.round_number,
                    "responses": len(round_.responses),
                    "threshold": self._threshold,
                }
            },
        )
        responses = list(round_.responses.values())
        if len(responses) < 2:
            log.parliament.debug(
                "[parliament] convergence.check: skipped — need ≥2 responses",
                extra={"_fields": {"responses": len(responses)}},
            )
            return False

        if self._embedding_registry is None:
            if not self._degraded_warned:
                log.parliament.warning(
                    "[parliament] convergence: no embedding registry — "
                    "convergence detection disabled (always returns False)",
                    extra={"_fields": {"threshold": self._threshold}},
                )
                self._degraded_warned = True
            return False

        t0 = time.monotonic()
        try:
            mean_sim = await self.mean_similarity(responses)
        except Exception as exc:
            log.parliament.warning(
                "[parliament] convergence.check: similarity computation failed",
                exc_info=exc,
                extra={"_fields": {"round_number": round_.round_number}},
            )
            return False

        converged = mean_sim >= self._threshold
        log.parliament.info(
            "[parliament] convergence.check: exit",
            extra={
                "_fields": {
                    "round_number": round_.round_number,
                    "mean_similarity": mean_sim,
                    "threshold": self._threshold,
                    "converged": converged,
                    "duration_ms": (time.monotonic() - t0) * 1000.0,
                }
            },
        )
        return converged

    async def mean_similarity(self, responses: list[str]) -> float:
        """Embed responses, return mean pairwise cosine similarity.

        Returns 0.0 when fewer than 2 non-trivial pairs exist.
        """
        log.parliament.debug(
            "[parliament] convergence.mean_similarity: entry",
            extra={"_fields": {"responses": len(responses)}},
        )
        if self._embedding_registry is None:
            return 0.0
        if len(responses) < 2:
            return 0.0
        provider = self._embedding_registry.get()
        embeddings = await provider.embed(responses)
        return self._mean_pairwise_cosine(embeddings)

    def _mean_pairwise_cosine(self, embeddings: list[list[float]]) -> float:
        """Mean pairwise cosine similarity in O(n·d) (F076).

        Uses the sum-of-vectors identity over L2-NORMALIZED embeddings. For unit
        vectors ``u_i`` with ``S = Σ u_i``::

            Σ_{i<j} (u_i · u_j) = (|S|² − Σ|u_i|²) / 2

        Each non-zero embedding normalizes to ``|u_i|² = 1``; a zero vector keeps
        ``u_i = 0`` (``|u_i|² = 0``) so it contributes 0 to every pair it joins —
        byte-for-byte the legacy behaviour, where ``_cosine_similarity`` returned
        0.0 for any zero-norm operand. The denominator stays the full pair count
        ``C(n, 2)`` so zero vectors still count as participating positions.

        Identical math to the old O(n²·d) double loop, no quadratic pair loop.
        """
        n = len(embeddings)
        if n < 2:
            return 0.0
        dim = len(embeddings[0])
        sum_vec = [0.0] * dim
        sum_sq_norms = 0.0
        for vec in embeddings:
            # A length-mismatched vector is undefined against the others; the
            # legacy path scored every pair involving it as 0.0. Skipping it from
            # the running sum (while it still occupies a pair slot via ``n``)
            # reproduces that exactly.
            if len(vec) != dim:
                continue
            norm = math.sqrt(sum(x * x for x in vec))
            if norm == 0.0:
                continue  # zero vector → unit value 0, contributes 0 to its pairs
            inv = 1.0 / norm
            for k in range(dim):
                sum_vec[k] += vec[k] * inv
            sum_sq_norms += 1.0
        s_dot_s = sum(c * c for c in sum_vec)
        pair_sum = (s_dot_s - sum_sq_norms) / 2.0
        pair_count = n * (n - 1) / 2.0
        if pair_count == 0.0:
            return 0.0
        return pair_sum / pair_count
