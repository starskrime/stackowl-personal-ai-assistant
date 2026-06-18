"""EmbeddingProvider ABC — local-only embedding contract.

Implementations MUST NOT make external API calls. All embedding computation
must be local and self-hosted. The B8 boundary script enforces that no HTTP
client imports appear anywhere under ``stackowl.embeddings``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from stackowl.health.status import HealthStatus


class EmbeddingProvider(ABC):
    """Abstract contract for any local embedding provider.

    Every concrete implementation must:
      * Run entirely on the local machine (``is_local`` returns ``True``).
      * Expose a stable ``model_name`` for logging and health reporting.
      * Expose its embedding vector ``dimension`` for downstream stores.
    """

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns a list of float vectors.

        Implementations MUST preserve input order: ``result[i]`` corresponds
        to ``texts[i]``.
        """
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Embedding vector dimension. All vectors returned by ``embed`` have this length."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Human-readable model identifier (used in logs and health reports)."""
        ...

    @property
    @abstractmethod
    def is_local(self) -> bool:
        """Must return True. B8 enforces no external API calls are imported."""
        ...

    async def health_check(self) -> HealthStatus:
        """Default health check — concrete providers may override."""
        return HealthStatus(
            name=f"embedding_{self.model_name}",
            status="ok",
            message=None,
            latency_ms=0.0,
        )
