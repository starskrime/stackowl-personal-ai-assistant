"""LanceDB adapter — ANN vector search for committed_facts.

Wraps the synchronous ``lancedb`` library behind an async interface using
``run_in_executor``.  The table is created lazily on first upsert with a
schema inferred from the embedding dimension.  Metadata is stored as a
JSON-encoded TEXT column to side-step schema evolution issues across mixed
fact populations.

All live I/O paths gate on :class:`TestModeGuard`; unit tests must
monkey-patch ``TestModeGuard.assert_not_test_mode`` to exercise the
on-disk store.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.memory.bridge import HealthReport
from stackowl.memory.lancedb_helpers import (
    TABLE_NAME,
    EmbeddingDimensionMismatch,
    SearchResult,
    read_corpus_identity,
    sync_delete,
    sync_list_tables,
    sync_reindex,
    sync_search,
    sync_upsert,
    write_corpus_identity,
)

if TYPE_CHECKING:  # pragma: no cover — typing-only
    import lancedb  # type: ignore[import-untyped]

    from stackowl.embeddings.registry import EmbeddingRegistry


__all__ = ["LanceDBAdapter", "SearchResult"]


def _default_data_dir() -> Path:
    from stackowl.paths import StackowlHome
    return StackowlHome.lancedb_dir()


class LanceDBAdapter:
    """Async wrapper around a single LanceDB table holding committed-fact vectors."""

    def __init__(
        self,
        data_dir: Path | None = None,
        embedding_registry: EmbeddingRegistry | None = None,
    ) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[memory] lancedb.init: entry",
            extra={
                "_fields": {
                    "data_dir": str(data_dir) if data_dir else "<default>",
                }
            },
        )
        self._data_dir = data_dir or _default_data_dir()
        self._connection: lancedb.DBConnection | None = None
        # Optional — supplied by the assembly so health() can compare the stored
        # corpus identity against the live active model (F062). None in unit
        # tests that don't exercise the model-drift health surface.
        self._embedding_registry = embedding_registry
        # 4. EXIT
        log.memory.debug(
            "[memory] lancedb.init: exit",
            extra={"_fields": {"data_dir": str(self._data_dir)}},
        )

    # ----- public async API ----------------------------------------------------

    async def upsert(
        self, fact_id: str, embedding: list[float], metadata: dict[str, Any]
    ) -> None:
        """Upsert a single fact into the ANN table."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] lancedb.upsert: entry",
            extra={"_fields": {"fact_id": fact_id, "dim": len(embedding)}},
        )
        TestModeGuard.assert_not_test_mode("lancedb.upsert")
        loop = asyncio.get_event_loop()
        # 3. STEP — bounce sync code to executor
        try:
            await loop.run_in_executor(
                None, sync_upsert, self._connect(), fact_id, embedding, metadata
            )
        except EmbeddingDimensionMismatch as exc:
            # F066 — a model/dim swap. Handle LOUDLY and ABOVE the promoter's
            # generic B5 swallow (so it is never folded into a silent FTS-only
            # degrade). The fact is already in SQLite+FTS (SoT); the dream-worker
            # reembed phase rebuilds the table at the new dim and re-adds it.
            log.memory.warning(
                "[memory] lancedb.upsert: embedding dim/model swap — fact deferred "
                "to reindex (committed to SQLite+FTS, semantic recall on FTS until rebuild)",
                exc_info=exc,
                extra={"_fields": {"fact_id": fact_id, "active_dim": len(embedding)}},
            )
            return
        # 4. EXIT
        log.memory.debug(
            "[memory] lancedb.upsert: exit",
            extra={"_fields": {"fact_id": fact_id}},
        )

    async def search(
        self,
        query_embedding: list[float],
        limit: int = 10,
        filter_expr: str | None = None,
    ) -> list[SearchResult]:
        """Run an ANN search; returns an empty list on any failure."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] lancedb.search: entry",
            extra={
                "_fields": {
                    "limit": limit,
                    "has_filter": filter_expr is not None,
                }
            },
        )
        TestModeGuard.assert_not_test_mode("lancedb.search")
        loop = asyncio.get_event_loop()
        try:
            # 3. STEP — sync search inside executor
            results = await loop.run_in_executor(
                None,
                sync_search,
                self._connect(),
                list(query_embedding),
                limit,
                filter_expr,
            )
        except Exception as exc:
            # B5 — never crash callers on ANN failure
            log.memory.warning(
                "[memory] lancedb.search: failed — returning []",
                exc_info=exc,
                extra={"_fields": {"limit": limit}},
            )
            return []
        # 4. EXIT
        log.memory.debug(
            "[memory] lancedb.search: exit",
            extra={"_fields": {"n_results": len(results)}},
        )
        return results

    async def delete(self, fact_id: str) -> None:
        """Remove a fact from the ANN table (idempotent, best-effort)."""
        log.memory.debug(
            "[memory] lancedb.delete: entry",
            extra={"_fields": {"fact_id": fact_id}},
        )
        TestModeGuard.assert_not_test_mode("lancedb.delete")
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, sync_delete, self._connect(), fact_id)
        except Exception as exc:
            # B5 — log but do not raise; caller may be best-effort.
            log.memory.warning(
                "[memory] lancedb.delete: failed",
                exc_info=exc,
                extra={"_fields": {"fact_id": fact_id}},
            )
            return
        log.memory.debug(
            "[memory] lancedb.delete: exit",
            extra={"_fields": {"fact_id": fact_id}},
        )

    async def reindex(
        self,
        records: list[tuple[str, list[float], dict[str, Any]]],
        target_dim: int | None = None,
    ) -> int:
        """Batch-upsert ``records`` and return the count written.

        When ``target_dim`` differs from the existing corpus dim (F066 model/dim
        swap) the table is dropped + recreated at the new dim before the batch is
        written (build-from-SoT — the caller passes re-embedded committed facts).
        """
        log.memory.info(
            "[memory] lancedb.reindex: entry",
            extra={"_fields": {"batch_size": len(records), "target_dim": target_dim}},
        )
        TestModeGuard.assert_not_test_mode("memory.reindex")
        if not records:
            log.memory.debug("[memory] lancedb.reindex: exit — empty batch")
            return 0
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, sync_reindex, self._connect(), records, target_dim
        )
        log.memory.info(
            "[memory] lancedb.reindex: exit",
            extra={"_fields": {"written": len(records)}},
        )
        return len(records)

    async def set_corpus_identity(self, model: str, dim: int) -> None:
        """Persist the corpus ``(model, dim)`` sidecar (after a reindex)."""
        log.memory.info(
            "[memory] lancedb.set_corpus_identity: entry",
            extra={"_fields": {"model": model, "dim": dim}},
        )
        TestModeGuard.assert_not_test_mode("memory.set_corpus_identity")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, write_corpus_identity, self._connect(), model, dim
        )

    async def corpus_identity(self) -> tuple[str | None, int | None]:
        """Return the stored corpus ``(model, dim)``; ``(None, None)`` when absent.

        Absent = a legacy/untagged corpus (or empty dir): the caller treats it
        as a MISMATCH (never a match) per the F062 corpus-level gate.
        """
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None, read_corpus_identity, self._connect()
            )
        except Exception as exc:
            # B5 — a sidecar read failure must not crash recall; treat as absent.
            log.memory.warning(
                "[memory] lancedb.corpus_identity: read failed — treating as absent",
                exc_info=exc,
            )
            return (None, None)

    async def health(self) -> HealthReport:
        """Probe LanceDB readiness; report status + table presence + model drift."""
        log.memory.debug("[memory] lancedb.health: entry")
        t0 = time.monotonic()
        loop = asyncio.get_event_loop()
        try:
            tables = await loop.run_in_executor(
                None, sync_list_tables, self._connect()
            )
            corpus_model, corpus_dim = await loop.run_in_executor(
                None, read_corpus_identity, self._connect()
            )
        except Exception as exc:
            # B5
            log.memory.warning(
                "[memory] lancedb.health: probe failed",
                exc_info=exc,
            )
            return HealthReport(
                name="memory.lancedb",
                status="down",
                details={"error": str(exc)},
                latency_ms=(time.monotonic() - t0) * 1000.0,
            )
        latency_ms = (time.monotonic() - t0) * 1000.0
        details: dict[str, Any] = {
            "data_dir": str(self._data_dir),
            "has_table": TABLE_NAME in tables,
            "corpus_embedding_model": corpus_model,
            "corpus_dim": corpus_dim,
        }
        # F062 — when the active embedding model no longer matches the corpus the
        # vectors were written under, semantic recall is being served from a
        # poisoned ANN; surface that LOUDLY as degraded so it's operator-visible.
        status: str = "ok"
        if self._embedding_registry is not None:
            active_model = self._embedding_registry.active_model
            active_dim = self._embedding_registry.active_dim
            details["active_embedding_model"] = active_model
            details["active_dim"] = active_dim
            # Only a corpus that EXISTS but mismatches is degraded — an empty
            # corpus (no vectors yet, no sidecar) is a healthy fresh install.
            if corpus_model is not None and (
                corpus_model != active_model or corpus_dim != active_dim
            ):
                status = "degraded"
                log.memory.warning(
                    "[memory] lancedb.health: corpus/active embedding model drift",
                    extra={
                        "_fields": {
                            "corpus_embedding_model": corpus_model,
                            "corpus_dim": corpus_dim,
                            "active_embedding_model": active_model,
                            "active_dim": active_dim,
                        }
                    },
                )
        report = HealthReport(
            name="memory.lancedb",
            status=status,  # type: ignore[arg-type]
            details=details,
            latency_ms=latency_ms,
        )
        log.memory.debug(
            "[memory] lancedb.health: exit",
            extra={"_fields": dict(report.details, latency_ms=latency_ms)},
        )
        return report

    # ----- private -------------------------------------------------------------

    def _connect(self) -> lancedb.DBConnection:
        if self._connection is None:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            import lancedb as _lance

            self._connection = _lance.connect(str(self._data_dir))
        return self._connection
