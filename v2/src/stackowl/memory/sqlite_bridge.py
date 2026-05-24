"""SqliteMemoryBridge — SQLite-backed implementation of :class:`MemoryBridge`."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

import aiosqlite

from stackowl.exceptions import DuplicateFactError
from stackowl.infra.observability import log
from stackowl.memory.bridge import HealthReport, MemoryBridge
from stackowl.memory.models import MemoryRecord, StagedFact
from stackowl.memory.sqlite_helpers import (
    fts_recall,
    pack_embedding,
    row_to_staged,
    semantic_recall,
)

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.db.pool import DbPool
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.memory.lancedb_adapter import LanceDBAdapter


class SqliteMemoryBridge(MemoryBridge):
    """Full SQLite-backed :class:`MemoryBridge`.

    Implements both the pipeline interface (:meth:`retrieve`, :meth:`store`)
    and the knowledge-pipeline interface (:meth:`stage`, :meth:`recall`,
    :meth:`delete`, :meth:`list_staged`, :meth:`health`). Storage layout:
    ``staged_facts`` (pre-promotion), ``committed_facts`` (long-term),
    ``committed_facts_fts`` (FTS5 index synced at the application layer).
    """

    def __init__(
        self,
        db: DbPool,
        embedding_registry: EmbeddingRegistry | None = None,
        lancedb: LanceDBAdapter | None = None,
        semantic_search_enabled: bool = True,
    ) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[memory] sqlite_bridge.init: entry",
            extra={
                "_fields": {
                    "has_embeddings": embedding_registry is not None,
                    "has_lancedb": lancedb is not None,
                    "semantic_enabled": semantic_search_enabled,
                }
            },
        )
        self._db = db
        self._embeddings = embedding_registry
        self._lancedb = lancedb
        self._semantic_enabled = semantic_search_enabled
        # 4. EXIT
        log.memory.debug("[memory] sqlite_bridge.init: exit")

    # --- pipeline contract ------------------------------------------------------------

    async def retrieve(self, query: str, session_id: str) -> str:
        """Return formatted committed-fact context for the classify pipeline step."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] sqlite_bridge.retrieve: entry",
            extra={"_fields": {"session_id": session_id, "query_len": len(query)}},
        )
        records = await self.recall(query, limit=5)
        # 2. DECISION
        if not records:
            log.memory.debug(
                "[memory] sqlite_bridge.retrieve: exit — no matches",
                extra={"_fields": {"session_id": session_id}},
            )
            return ""
        # 3. STEP — format as bullet list with header
        lines = ["Prior context:"]
        lines.extend(f"- {r.content}" for r in records)
        out = "\n".join(lines)
        # 4. EXIT
        log.memory.debug(
            "[memory] sqlite_bridge.retrieve: exit",
            extra={
                "_fields": {
                    "session_id": session_id,
                    "context_len": len(out),
                    "n_records": len(records),
                }
            },
        )
        return out

    async def store(self, content: str, session_id: str) -> None:
        """Store conversation content as a staged fact (source_type=conversation)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] sqlite_bridge.store: entry",
            extra={"_fields": {"session_id": session_id, "content_len": len(content)}},
        )
        fact = StagedFact(
            content=content,
            source_type="conversation",
            source_ref=session_id,
            confidence=0.5,
        )
        # 3. STEP
        await self.stage(fact)
        # 4. EXIT
        log.memory.debug(
            "[memory] sqlite_bridge.store: exit",
            extra={"_fields": {"fact_id": fact.fact_id, "session_id": session_id}},
        )

    # --- knowledge-pipeline contract --------------------------------------------------

    async def stage(self, fact: StagedFact) -> None:
        """Insert a fact into the staged queue. Raises DuplicateFactError on collision."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] sqlite_bridge.stage: entry",
            extra={"_fields": {"fact_id": fact.fact_id, "source_type": fact.source_type}},
        )
        embedding_blob = pack_embedding(fact.embedding)
        try:
            # 3. STEP — write to DB
            await self._db.execute(
                """INSERT INTO staged_facts (
                       fact_id, content, source_type, source_ref, confidence,
                       staged_at, reinforcement_count, status, embedding, embedding_model
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fact.fact_id,
                    fact.content,
                    fact.source_type,
                    fact.source_ref,
                    fact.confidence,
                    fact.staged_at.isoformat(),
                    fact.reinforcement_count,
                    fact.status,
                    embedding_blob,
                    fact.embedding_model,
                ),
            )
        except aiosqlite.IntegrityError as exc:
            # B5: every except logs at warning+
            log.memory.warning(
                "[memory] sqlite_bridge.stage: duplicate fact_id",
                exc_info=exc,
                extra={"_fields": {"fact_id": fact.fact_id}},
            )
            raise DuplicateFactError(fact.fact_id) from exc
        # 4. EXIT
        log.memory.info(
            "[memory] sqlite_bridge.stage: exit",
            extra={"_fields": {"fact_id": fact.fact_id, "source_type": fact.source_type}},
        )

    async def recall(self, query: str, limit: int = 10) -> list[MemoryRecord]:
        """Semantic recall via LanceDB when enabled; FTS5 BM25 fallback otherwise."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] sqlite_bridge.recall: entry",
            extra={"_fields": {"query_len": len(query), "limit": limit}},
        )
        # 2. DECISION — try the semantic path when wired and embedder available
        if (
            self._semantic_enabled
            and self._lancedb is not None
            and self._embeddings is not None
        ):
            semantic = await semantic_recall(
                self._db, self._embeddings, self._lancedb, query, limit
            )
            if semantic is not None:
                log.memory.debug(
                    "[memory] sqlite_bridge.recall: exit — semantic",
                    extra={"_fields": {"n_results": len(semantic)}},
                )
                return semantic
        # 3. STEP — FTS5 BM25 fallback
        records = await fts_recall(self._db, query, limit)
        # 4. EXIT
        log.memory.debug(
            "[memory] sqlite_bridge.recall: exit — fts5",
            extra={"_fields": {"n_results": len(records), "limit": limit}},
        )
        return records

    async def delete(self, fact_id: str) -> None:
        """Delete a fact from all stores (sqlite + lancedb when present)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] sqlite_bridge.delete: entry",
            extra={"_fields": {"fact_id": fact_id}},
        )
        # 3. STEP — clean FTS first (need committed rowids), then delete rows
        committed_rows = await self._db.fetch_all(
            "SELECT rowid AS rowid FROM committed_facts WHERE fact_id = ?",
            (fact_id,),
        )
        for row in committed_rows:
            await self._db.execute(
                "DELETE FROM committed_facts_fts WHERE rowid = ?",
                (row["rowid"],),
            )
        await self._db.execute("DELETE FROM committed_facts WHERE fact_id = ?", (fact_id,))
        await self._db.execute("DELETE FROM staged_facts WHERE fact_id = ?", (fact_id,))
        # 3. STEP — best-effort delete from LanceDB
        if self._lancedb is not None:
            try:
                await self._lancedb.delete(fact_id)
            except Exception as exc:
                # B5 — never let ANN failures bubble out of delete()
                log.memory.warning(
                    "[memory] sqlite_bridge.delete: lancedb delete failed",
                    exc_info=exc,
                    extra={"_fields": {"fact_id": fact_id}},
                )
        # 4. EXIT
        log.memory.info(
            "[memory] sqlite_bridge.delete: exit",
            extra={
                "_fields": {
                    "fact_id": fact_id,
                    "committed_rows": len(committed_rows),
                }
            },
        )

    async def list_staged(
        self, status: Literal["staged", "committed", "rejected"] = "staged"
    ) -> list[StagedFact]:
        """Return staged facts filtered by status, newest first."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] sqlite_bridge.list_staged: entry",
            extra={"_fields": {"status": status}},
        )
        rows = await self._db.fetch_all(
            """SELECT fact_id, content, source_type, source_ref, confidence,
                      staged_at, reinforcement_count, status, embedding, embedding_model
               FROM staged_facts
               WHERE status = ?
               ORDER BY staged_at DESC""",
            (status,),
        )
        results = [row_to_staged(row) for row in rows]
        # 4. EXIT
        log.memory.debug(
            "[memory] sqlite_bridge.list_staged: exit",
            extra={"_fields": {"status": status, "n_results": len(results)}},
        )
        return results

    async def health(self) -> HealthReport:
        """Probe SQLite connectivity and report staged/committed row counts."""
        # 1. ENTRY
        log.memory.debug("[memory] sqlite_bridge.health: entry")
        t0 = time.monotonic()
        try:
            await self._db.fetch_all("SELECT 1")
            staged = await self._db.fetch_all("SELECT COUNT(*) AS cnt FROM staged_facts")
            committed = await self._db.fetch_all("SELECT COUNT(*) AS cnt FROM committed_facts")
            latency_ms = (time.monotonic() - t0) * 1000.0
            report = HealthReport(
                name="memory.sqlite",
                status="ok",
                details={
                    "staged_count": int(staged[0]["cnt"]),
                    "committed_count": int(committed[0]["cnt"]),
                },
                latency_ms=latency_ms,
            )
        except Exception as exc:
            # B5: log warning on any exception
            log.memory.warning(
                "[memory] sqlite_bridge.health: probe failed",
                exc_info=exc,
                extra={"_fields": {}},
            )
            return HealthReport(
                name="memory.sqlite",
                status="degraded",
                details={"error": str(exc)},
                latency_ms=0.0,
            )
        # 4. EXIT
        log.memory.debug(
            "[memory] sqlite_bridge.health: exit",
            extra={"_fields": dict(report.details, latency_ms=report.latency_ms)},
        )
        return report
