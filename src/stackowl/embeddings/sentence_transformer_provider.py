"""SentenceTransformerProvider — semantic embeddings via a local model.

Loads the requested sentence-transformers model from the local cache on a
background thread (so process startup is not blocked). ``embed`` runs the
encode call in the default executor to avoid blocking the event loop.

Loading prefers the local cache (offline, quiet) but self-heals on a cache
miss: it retries once with network allowed, downloading the model via the
same ``SentenceTransformer(...)`` call ``stackowl models pull`` uses. Only a
genuine failure (no network, bad model name) falls through to the caller,
which is what makes ``EmbeddingRegistry`` degrade to the cruder hash
fallback. An operator who explicitly sets ``HF_HUB_OFFLINE`` themselves is
respected — no surprise network call. ``stackowl models pull`` remains
available for manually pre-warming the cache ahead of time.
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
        """Load the sentence-transformers model (executed inside the thread pool).

        SELF-HEAL, not self-mask: a cache miss triggers ONE bounded retry with
        network allowed (same download path as `stackowl models pull`) before
        giving up — the model gets fetched, not permanently replaced by the
        cruder hash fallback in EmbeddingRegistry. An operator who explicitly
        set HF_HUB_OFFLINE themselves is respected — no surprise network call.
        """
        # setdefault so an explicit operator override still wins; recorded
        # BEFORE setdefault so we know whether OUR default or the operator's
        # own setting is in effect (only OUR default gets the retry).
        operator_forced_offline = "HF_HUB_OFFLINE" in os.environ
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
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

        def _attempt() -> SentenceTransformer:
            # Pin CPU: this host's GPU driver is too old for the bundled torch
            # build, so sentence-transformers would probe CUDA, warn, then fall
            # back to CPU anyway. device="cpu" skips that probe. Scoped to this
            # model only — image generation keeps its own CUDA path.
            return SentenceTransformer(  # type: ignore[no-any-return]
                self._model_name, cache_folder=cache_dir, device="cpu"
            )

        try:
            self._model = _attempt()
        except Exception as exc:
            if operator_forced_offline:
                log.engine.error(
                    "[embeddings] SentenceTransformerProvider: model not cached and "
                    "HF_HUB_OFFLINE was explicitly set by the operator — not downloading",
                    exc_info=exc,
                    extra={"_fields": {"model": self._model_name, "cache_dir": cache_dir}},
                )
                raise
            log.engine.warning(
                "[embeddings] SentenceTransformerProvider: model not cached — "
                "self-heal retry with network allowed (same path as `stackowl models pull`)",
                extra={"_fields": {"model": self._model_name, "cache_dir": cache_dir}},
            )
            os.environ.pop("HF_HUB_OFFLINE", None)
            os.environ.pop("TRANSFORMERS_OFFLINE", None)
            try:
                self._model = _attempt()
            except Exception as download_exc:
                log.engine.error(
                    "[embeddings] SentenceTransformerProvider: self-heal download failed "
                    "— genuinely no network, falling back to hash embeddings",
                    exc_info=download_exc,
                    extra={"_fields": {"model": self._model_name, "cache_dir": cache_dir}},
                )
                raise
            finally:
                os.environ.setdefault("HF_HUB_OFFLINE", "1")
                os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        assert self._model is not None
        # get_sentence_embedding_dimension() was renamed to
        # get_embedding_dimension(); prefer the new name, fall back for
        # older library versions.
        get_dim = getattr(self._model, "get_embedding_dimension", None)
        raw_dim: Any = (
            get_dim() if callable(get_dim) else self._model.get_sentence_embedding_dimension()
        )
        if raw_dim is None:
            raise RuntimeError(f"Model {self._model_name} reported no embedding dimension")
        dim: int = int(raw_dim)
        self._dim = dim
        log.engine.info(
            "[embeddings] SentenceTransformerProvider: model loaded",
            extra={"_fields": {"model": self._model_name, "dim": dim, "cache_dir": cache_dir}},
        )

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
