"""SentenceTransformerProvider — semantic embeddings via a local model.

Loads the requested sentence-transformers model from the local cache on a
background thread (so process startup is not blocked). ``embed`` runs the
encode call in the default executor to avoid blocking the event loop.

This module deliberately does NOT import any HTTP client. sentence-transformers
will use its bundled cache; if the model is missing, ``stackowl models pull``
downloads it explicitly via the same library.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

from stackowl.embeddings.base import EmbeddingProvider
from stackowl.infra.observability import log

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_DEFAULT_MODEL = "all-MiniLM-L6-v2"


class SentenceTransformerProvider(EmbeddingProvider):
    """Semantic embedding provider backed by a locally cached transformer."""

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        self._model_name: str = model_name
        self._model: SentenceTransformer | None = None
        self._dim: int | None = None

    @classmethod
    async def create(cls, model_name: str = _DEFAULT_MODEL) -> SentenceTransformerProvider:
        """Factory: instantiates the provider and loads the model off the event loop."""
        # 1. ENTRY
        log.engine.debug(
            "[embeddings] sentence_transformer.create: entry",
            extra={"_fields": {"model": model_name}},
        )

        instance = cls(model_name)
        loop = asyncio.get_event_loop()
        try:
            # 3. STEP — load is heavy, push to executor
            await loop.run_in_executor(None, instance._load_model)
        except Exception as exc:
            log.engine.error(
                "[embeddings] sentence_transformer.create: model load failed",
                exc_info=exc,
                extra={"_fields": {"model": model_name}},
            )
            raise

        # 4. EXIT
        log.engine.debug(
            "[embeddings] sentence_transformer.create: exit",
            extra={"_fields": {"model": model_name, "dim": instance._dim}},
        )
        return instance

    def _load_model(self) -> None:
        """Load the sentence-transformers model (executed inside the thread pool)."""
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            log.engine.error(
                "[embeddings] sentence_transformer._load_model: sentence-transformers not installed",
                exc_info=exc,
                extra={"_fields": {"model": self._model_name}},
            )
            raise

        cache_dir = os.environ.get("STACKOWL_MODEL_CACHE_DIR")
        try:
            self._model = SentenceTransformer(self._model_name, cache_folder=cache_dir)
            assert self._model is not None
            raw_dim: Any = self._model.get_sentence_embedding_dimension()
            if raw_dim is None:
                raise RuntimeError(f"Model {self._model_name} reported no embedding dimension")
            dim: int = int(raw_dim)
            self._dim = dim
            log.engine.info(
                "[embeddings] SentenceTransformerProvider: model loaded",
                extra={"_fields": {"model": self._model_name, "dim": dim, "cache_dir": cache_dir}},
            )
        except Exception as exc:
            log.engine.error(
                "[embeddings] SentenceTransformerProvider: model load failed",
                exc_info=exc,
                extra={"_fields": {"model": self._model_name, "cache_dir": cache_dir}},
            )
            raise

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # 1. ENTRY
        log.engine.debug(
            "[embeddings] sentence_transformer.embed: entry",
            extra={"_fields": {"batch_size": len(texts), "model": self._model_name}},
        )

        if self._model is None:
            log.engine.error(
                "[embeddings] sentence_transformer.embed: model not loaded",
                extra={"_fields": {"model": self._model_name}},
            )
            raise RuntimeError("Model not loaded — use SentenceTransformerProvider.create()")

        start = time.monotonic()
        try:
            # 3. STEP — encode runs on executor
            loop = asyncio.get_event_loop()
            vectors = await loop.run_in_executor(None, self._embed_sync, texts)
        except Exception as exc:
            log.engine.error(
                "[embeddings] sentence_transformer.embed: encode failed",
                exc_info=exc,
                extra={"_fields": {"batch_size": len(texts), "model": self._model_name}},
            )
            raise

        duration_ms = (time.monotonic() - start) * 1000.0
        # 4. EXIT
        log.engine.debug(
            "[embeddings] sentence_transformer.embed: exit",
            extra={
                "_fields": {
                    "batch_size": len(texts),
                    "vectors": len(vectors),
                    "duration_ms": duration_ms,
                    "model": self._model_name,
                }
            },
        )
        return vectors

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        assert self._model is not None
        raw: Any = self._model.encode(texts, convert_to_numpy=True)
        return [list(map(float, v)) for v in raw]

    @property
    def dimension(self) -> int:
        return self._dim if self._dim is not None else 384

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def is_local(self) -> bool:
        return True
