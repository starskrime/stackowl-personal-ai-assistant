"""HashEmbeddingProvider — deterministic fallback, no model required.

Produces a 384-d pseudo-embedding by seeding numpy's default RNG with the
first 4 bytes of SHA-256(text). Reproducible, zero external dependencies
beyond numpy, zero cost. Used when no semantic model is available so the
rest of the pipeline (storage, search, ranking) keeps working.
"""

from __future__ import annotations

import asyncio
import hashlib
import time

import numpy as np

from stackowl.embeddings.base import EmbeddingProvider
from stackowl.infra.observability import log

_DIM = 384
_MODEL_NAME = "hash-v1-384d"
_WARNED = False  # module-level: log "degraded mode" warning only once per process


class HashEmbeddingProvider(EmbeddingProvider):
    """Deterministic pseudo-embedding via SHA-256 + seeded numpy projection."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # 1. ENTRY
        log.engine.debug(
            "[embeddings] hash.embed: entry",
            extra={"_fields": {"batch_size": len(texts), "model": _MODEL_NAME}},
        )

        global _WARNED
        if not _WARNED:
            # 2. DECISION — first call in this process, surface the fallback state
            log.engine.warning(
                "[embeddings] hash fallback active — run `stackowl models pull all-MiniLM-L6-v2` "
                "for semantic embeddings",
                extra={"_fields": {"model": _MODEL_NAME, "dim": _DIM}},
            )
            _WARNED = True

        # 3. STEP — push the CPU work into a thread to keep the loop responsive
        start = time.monotonic()
        try:
            loop = asyncio.get_event_loop()
            vectors = await loop.run_in_executor(None, self._embed_sync, texts)
        except Exception as exc:
            log.engine.error(
                "[embeddings] hash.embed: failed",
                exc_info=exc,
                extra={"_fields": {"batch_size": len(texts)}},
            )
            raise

        duration_ms = (time.monotonic() - start) * 1000.0
        # 4. EXIT
        log.engine.debug(
            "[embeddings] hash.embed: exit",
            extra={
                "_fields": {
                    "batch_size": len(texts),
                    "vectors": len(vectors),
                    "duration_ms": duration_ms,
                }
            },
        )
        return vectors

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        result: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            seed = int.from_bytes(digest[:4], "big")
            rng = np.random.default_rng(seed)
            vec: list[float] = rng.standard_normal(_DIM).tolist()
            result.append(vec)
        return result

    @property
    def dimension(self) -> int:
        return _DIM

    @property
    def model_name(self) -> str:
        return _MODEL_NAME

    @property
    def is_local(self) -> bool:
        return True
