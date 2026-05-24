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

import platformdirs

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.memory.bridge import HealthReport
from stackowl.memory.lancedb_helpers import (
    TABLE_NAME,
    SearchResult,
    sync_delete,
    sync_list_tables,
    sync_reindex,
    sync_search,
    sync_upsert,
)

if TYPE_CHECKING:  # pragma: no cover — typing-only
    import lancedb  # type: ignore[import-untyped]


__all__ = ["LanceDBAdapter", "SearchResult"]


def _default_data_dir() -> Path:
    """Resolve the default lancedb data directory under platformdirs."""
    return Path(platformdirs.user_data_dir("stackowl")) / "lancedb"


class LanceDBAdapter:
    """Async wrapper around a single LanceDB table holding committed-fact vectors."""

    def __init__(self, data_dir: Path | None = None) -> None:
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
        await loop.run_in_executor(
            None, sync_upsert, self._connect(), fact_id, embedding, metadata
        )
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
        self, records: list[tuple[str, list[float], dict[str, Any]]]
    ) -> int:
        """Batch-upsert ``records`` and return the count written."""
        log.memory.info(
            "[memory] lancedb.reindex: entry",
            extra={"_fields": {"batch_size": len(records)}},
        )
        TestModeGuard.assert_not_test_mode("memory.reindex")
        if not records:
            log.memory.debug("[memory] lancedb.reindex: exit — empty batch")
            return 0
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, sync_reindex, self._connect(), records)
        log.memory.info(
            "[memory] lancedb.reindex: exit",
            extra={"_fields": {"written": len(records)}},
        )
        return len(records)

    async def health(self) -> HealthReport:
        """Probe LanceDB readiness; report status + table presence."""
        log.memory.debug("[memory] lancedb.health: entry")
        t0 = time.monotonic()
        loop = asyncio.get_event_loop()
        try:
            tables = await loop.run_in_executor(
                None, sync_list_tables, self._connect()
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
        report = HealthReport(
            name="memory.lancedb",
            status="ok",
            details={
                "data_dir": str(self._data_dir),
                "has_table": TABLE_NAME in tables,
            },
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
