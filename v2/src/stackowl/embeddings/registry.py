"""EmbeddingRegistry — owns the active EmbeddingProvider with a hash fallback.

At construction time the registry tries to bring up the semantic
``SentenceTransformerProvider``. If that fails for any reason (model not
downloaded, library missing, hardware issue), it silently degrades to the
``HashEmbeddingProvider`` so the rest of the platform keeps functioning.
The degraded state is surfaced via ``health_check``.
"""

from __future__ import annotations

from stackowl.embeddings.base import EmbeddingProvider
from stackowl.health.status import HealthStatus
from stackowl.infra.observability import log

_DEFAULT_MODEL = "all-MiniLM-L6-v2"


class EmbeddingRegistry:
    """Holds the active EmbeddingProvider. Falls back to hash if model unavailable."""

    def __init__(self) -> None:
        self._provider: EmbeddingProvider | None = None
        self._is_semantic: bool = False
        self._model_name: str = _DEFAULT_MODEL

    @classmethod
    async def create(cls, model_name: str = _DEFAULT_MODEL) -> EmbeddingRegistry:
        """Factory: tries SentenceTransformerProvider, falls back to HashEmbeddingProvider."""
        # 1. ENTRY
        log.engine.debug(
            "[embeddings] registry.create: entry",
            extra={"_fields": {"model": model_name}},
        )

        registry = cls()
        registry._model_name = model_name

        try:
            # 2. DECISION — try semantic first
            from stackowl.embeddings.sentence_transformer_provider import (
                SentenceTransformerProvider,
            )

            provider: EmbeddingProvider = await SentenceTransformerProvider.create(model_name)
            registry._provider = provider
            registry._is_semantic = True
            log.engine.info(
                "[embeddings] registry: semantic provider active",
                extra={"_fields": {"model": model_name, "dim": provider.dimension}},
            )
        except Exception as exc:
            # 2. DECISION — semantic unavailable, degrade to hash
            log.engine.warning(
                "[embeddings] registry: falling back to hash provider",
                exc_info=exc,
                extra={"_fields": {"requested_model": model_name}},
            )
            from stackowl.embeddings.hash_provider import HashEmbeddingProvider

            registry._provider = HashEmbeddingProvider()
            registry._is_semantic = False

        # 4. EXIT
        log.engine.debug(
            "[embeddings] registry.create: exit",
            extra={
                "_fields": {
                    "model": registry._model_name,
                    "semantic": registry._is_semantic,
                    "active_provider": registry._provider.model_name if registry._provider else None,
                }
            },
        )
        return registry

    def get(self) -> EmbeddingProvider:
        """Return the active provider, lazily defaulting to hash if uninitialised."""
        if self._provider is None:
            log.engine.warning(
                "[embeddings] registry.get: no provider initialised — defaulting to hash",
                extra={"_fields": {"requested_model": self._model_name}},
            )
            from stackowl.embeddings.hash_provider import HashEmbeddingProvider

            self._provider = HashEmbeddingProvider()
            self._is_semantic = False
        return self._provider

    @property
    def contributor_name(self) -> str:
        return "embedding_registry"

    @property
    def is_semantic(self) -> bool:
        return self._is_semantic

    async def health_check(self) -> HealthStatus:
        status: str = "ok" if self._is_semantic else "degraded"
        msg = None if self._is_semantic else "Hash fallback active — run `stackowl models pull` for semantic embeddings"
        return HealthStatus(
            name=self.contributor_name,
            status=status,  # type: ignore[arg-type]
            message=msg,
            latency_ms=0.0,
        )
