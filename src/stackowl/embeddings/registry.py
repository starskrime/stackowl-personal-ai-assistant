"""EmbeddingRegistry — owns the active EmbeddingProvider with a hash fallback.

At construction time the registry tries to bring up the semantic
``SentenceTransformerProvider``. If that fails for any reason (model not
downloaded, library missing, hardware issue), it silently degrades to the
``HashEmbeddingProvider`` so the rest of the platform keeps functioning.
The degraded state is surfaced via ``health_check``.
"""

from __future__ import annotations

from collections.abc import Callable

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

    @property
    def active_model(self) -> str:
        """The active provider's model id — the SINGLE corpus-identity authority.

        ``"hash-v1-384d"`` when degraded to the hash fallback, the
        sentence-transformer name when semantic. Callers MUST key corpus-match
        decisions on this, never on a metadata default.
        """
        return self.get().model_name

    @property
    def active_dim(self) -> int:
        """The active provider's embedding dimension (paired with ``active_model``)."""
        return self.get().dimension

    async def health_check(self) -> HealthStatus:
        status: str = "ok" if self._is_semantic else "degraded"
        msg = None if self._is_semantic else "Hash fallback active — run `stackowl models pull` for semantic embeddings"
        return HealthStatus(
            name=self.contributor_name,
            status=status,  # type: ignore[arg-type]
            message=msg,
            latency_ms=0.0,
        )

    # ---- HealableResource protocol (ADR-6 F-87) ---------------------------
    # Mirrors ModelProvider's shape (providers/base.py): a boot-time failure
    # here is not a dead handle to recycle, it's a degrade-to-hash decision
    # that the periodic health sweep can retry via ensure_available().

    @property
    def available(self) -> bool:
        return self._is_semantic

    @property
    def unavailable_reason(self) -> str | None:
        if self._is_semantic:
            return None
        return "hash fallback active — semantic model unavailable"

    async def ensure_available(self) -> None:
        """Retry the semantic provider load; raise if it cannot be recovered.

        No-op when already semantic. Otherwise attempts
        ``SentenceTransformerProvider.create(self._model_name)`` again (that
        classmethod already has its own bounded network-retry, commit
        0ba23e52); on success swaps ``self._provider``/``self._is_semantic``
        to the semantic provider so the registry self-heals instead of
        staying on the cruder hash fallback forever. On failure the registry
        stays on hash and the exception propagates — the sweep's
        RecoveryActuator owns backoff, not this method.
        """
        # 1. ENTRY
        log.engine.debug(
            "[embeddings] registry.ensure_available: entry",
            extra={"_fields": {"semantic": self._is_semantic, "model": self._model_name}},
        )
        if self._is_semantic:
            # 2. DECISION — already healthy, nothing to retry
            log.engine.debug(
                "[embeddings] registry.ensure_available: already semantic — no-op",
                extra={"_fields": {"model": self._model_name}},
            )
            return

        # 2. DECISION — degraded; retry the semantic load
        from stackowl.embeddings.sentence_transformer_provider import (
            SentenceTransformerProvider,
        )

        try:
            # 3. STEP — self-heal attempt
            provider = await SentenceTransformerProvider.create(self._model_name)
        except Exception as exc:
            log.engine.warning(
                "[embeddings] registry.ensure_available: self-heal retry failed — "
                "staying on hash fallback",
                exc_info=exc,
                extra={"_fields": {"model": self._model_name}},
            )
            raise

        self._provider = provider
        self._is_semantic = True
        # 4. EXIT
        log.engine.info(
            "[embeddings] self-heal: semantic embeddings restored",
            extra={"_fields": {"model": self._model_name, "dim": provider.dimension}},
        )

    def register_on_recycled(self, cb: Callable[[], None]) -> None:
        """No-op: this resource has no downstream dependents to notify today.

        Mirrors ModelProvider.register_on_recycled (providers/base.py) — a
        registry swap happens in-place (``get()`` always returns the current
        ``self._provider``), so callers never hold a stale reference that
        needs invalidating.
        """
        log.engine.debug(
            "[embeddings] registry.register_on_recycled: no-op (no downstream dependents)",
            extra={"_fields": {"model": self._model_name}},
        )
