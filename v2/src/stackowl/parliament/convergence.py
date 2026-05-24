"""ConvergenceDetector — cosine-similarity-based agreement detection."""

from __future__ import annotations

import math
import time

from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.infra.observability import log
from stackowl.parliament.models import ParliamentRound

_DEGRADED_WARNED = False


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
            global _DEGRADED_WARNED
            if not _DEGRADED_WARNED:
                log.parliament.warning(
                    "[parliament] convergence: no embedding registry — "
                    "convergence detection disabled (always returns False)",
                    extra={"_fields": {"threshold": self._threshold}},
                )
                _DEGRADED_WARNED = True
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
        pair_count = 0
        total = 0.0
        for i in range(len(embeddings)):
            for j in range(i + 1, len(embeddings)):
                total += self._cosine_similarity(embeddings[i], embeddings[j])
                pair_count += 1
        if pair_count == 0:
            return 0.0
        return total / pair_count

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Pure-Python cosine similarity. Returns 0.0 if either vector is zero."""
        if len(a) != len(b):
            return 0.0
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for x, y in zip(a, b, strict=True):
            dot += x * y
            norm_a += x * x
            norm_b += y * y
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
