"""KuzuAdapter — async wrapper around an on-disk Kuzu knowledge graph.

The graph stores two node types — ``Fact`` and ``Entity`` — joined by two
relationship types: ``MENTIONS`` (Fact -> Entity) and ``RELATED_TO``
(Entity -> Entity, strength-weighted). Schema is created lazily in the
constructor; node upserts use delete-then-insert because Kuzu 0.x has no
``MERGE``.

THREAD-CONFINEMENT INVARIANT (F067): a ``kuzu.Connection`` is NOT thread-safe.
ALL Connection access is confined to ONE dedicated worker thread — a
``ThreadPoolExecutor(max_workers=1)``. Every blocking op is bounced through
``self._executor`` (NEVER ``None`` = the default multi-worker pool), so a live
``classify`` traverse and a dream-worker ``kuzu_sync`` upsert can never drive
the same Connection from two threads. Serialization is the cost (a long upsert
batch delays a live traverse) — bounded by chunking the dream-worker writer.
The executor is shut down in :meth:`aclose`.

``F067-followup`` (NOT fixed here): node upsert is delete-then-insert and is
non-atomic across a crash (a process death between the DELETE and the INSERT
loses the node). Single-thread confinement removes the cross-thread RACE only;
the atomic crash window needs the delete+insert wrapped in one Kuzu transaction
— tracked as a separate follow-up.

All live I/O paths gate on :class:`TestModeGuard`; unit tests must
monkey-patch ``TestModeGuard.assert_not_test_mode`` to exercise the
on-disk store.
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.memory.bridge import HealthReport
from stackowl.memory.kuzu_helpers import (
    sync_create_schema,
    sync_link_entities,
    sync_link_fact_to_entity,
    sync_probe,
    sync_traverse,
    sync_upsert_entity,
    sync_upsert_fact,
)

if TYPE_CHECKING:  # pragma: no cover — typing-only
    import kuzu


__all__ = ["KuzuAdapter"]


def _default_data_dir() -> Path:
    from stackowl.paths import StackowlHome
    return StackowlHome.kuzu_dir()


class KuzuAdapter:
    """Async wrapper around a single Kuzu database holding the entity graph."""

    def __init__(self, data_dir: Path | None = None) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.init: entry",
            extra={
                "_fields": {
                    "data_dir": str(data_dir) if data_dir else "<default>",
                }
            },
        )
        self._data_dir = data_dir or _default_data_dir()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        # Kuzu treats the path argument as the database file/folder itself;
        # passing a pre-existing empty directory raises. Anchor the database
        # inside ``data_dir`` so the directory stays our scoped sandbox.
        self._db_path = self._data_dir / "graph.kuzu"
        # F067 — the dedicated single Kuzu worker thread. ALL Connection access
        # (including creation + schema bootstrap below) is confined to it so the
        # non-thread-safe Connection is never touched concurrently.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="kuzu"
        )
        # 2. DECISION — lazy import keeps test collection cheap when kuzu is absent
        import kuzu as _kuzu

        self._kuzu_mod = _kuzu
        # Create the Database + Connection ON the worker thread (Kuzu may pin a
        # Connection to its creating thread) and bootstrap the schema there too,
        # so the very first access is already on the confined thread.
        self._db: kuzu.Database = _kuzu.Database(str(self._db_path))
        self._conn: kuzu.Connection = self._executor.submit(
            self._make_conn_and_schema
        ).result()
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.init: exit",
            extra={"_fields": {"data_dir": str(self._data_dir)}},
        )

    def _make_conn_and_schema(self) -> kuzu.Connection:
        """Create the Connection + bootstrap schema — runs on the worker thread."""
        conn = self._kuzu_mod.Connection(self._db)
        try:
            sync_create_schema(conn)
        except Exception as exc:
            # B5 — a failed bootstrap must surface (hard-fail per assembly policy).
            log.memory.error(
                "[memory] kuzu.init: schema bootstrap failed",
                exc_info=exc,
                extra={"_fields": {"data_dir": str(self._data_dir)}},
            )
            raise
        return conn

    # ----- public async API ----------------------------------------------------

    async def upsert_entity(
        self,
        entity_id: str,
        name: str,
        entity_type: str,
        source_fact_id: str,
    ) -> None:
        """Upsert (delete-then-insert) an Entity node keyed by ``entity_id``."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.upsert_entity: entry",
            extra={"_fields": {"entity_id": entity_id, "entity_type": entity_type}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.upsert_entity")
        loop = asyncio.get_event_loop()
        # 3. STEP — sync upsert in executor
        await loop.run_in_executor(
            self._executor,
            sync_upsert_entity,
            self._conn,
            entity_id,
            name,
            entity_type,
            source_fact_id,
        )
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.upsert_entity: exit",
            extra={"_fields": {"entity_id": entity_id}},
        )

    async def upsert_fact_node(
        self,
        fact_id: str,
        content: str,
        confidence: float,
    ) -> None:
        """Upsert (delete-then-insert) a Fact node keyed by ``fact_id``."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.upsert_fact_node: entry",
            extra={"_fields": {"fact_id": fact_id, "confidence": confidence}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.upsert_fact_node")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(
            self._executor, sync_upsert_fact, self._conn, fact_id, content, confidence
        )
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.upsert_fact_node: exit",
            extra={"_fields": {"fact_id": fact_id}},
        )

    async def link_fact_to_entity(
        self,
        fact_id: str,
        entity_id: str,
        mention_type: str = "mentions",
    ) -> None:
        """Create a Fact -> Entity MENTIONS edge."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.link_fact_to_entity: entry",
            extra={
                "_fields": {
                    "fact_id": fact_id,
                    "entity_id": entity_id,
                    "mention_type": mention_type,
                }
            },
        )
        TestModeGuard.assert_not_test_mode("kuzu.link_fact_to_entity")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(
            self._executor,
            sync_link_fact_to_entity,
            self._conn,
            fact_id,
            entity_id,
            mention_type,
        )
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.link_fact_to_entity: exit",
            extra={"_fields": {"fact_id": fact_id, "entity_id": entity_id}},
        )

    async def link_entities(
        self,
        from_id: str,
        to_id: str,
        relation: str,
        strength: float = 1.0,
    ) -> None:
        """Create an Entity -> Entity RELATED_TO edge with the given strength."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.link_entities: entry",
            extra={
                "_fields": {
                    "from_id": from_id,
                    "to_id": to_id,
                    "relation": relation,
                    "strength": strength,
                }
            },
        )
        TestModeGuard.assert_not_test_mode("kuzu.link_entities")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(
            self._executor,
            sync_link_entities,
            self._conn,
            from_id,
            to_id,
            relation,
            strength,
        )
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.link_entities: exit",
            extra={"_fields": {"from_id": from_id, "to_id": to_id}},
        )

    async def traverse(
        self, entity_id: str, max_hops: int = 2
    ) -> list[dict[str, Any]]:
        """BFS over RELATED_TO edges. Returns ``[]`` on any failure."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.traverse: entry",
            extra={"_fields": {"entity_id": entity_id, "max_hops": max_hops}},
        )
        # 2. DECISION — defensive: never raise into caller
        loop = asyncio.get_event_loop()
        try:
            rows = await loop.run_in_executor(
                self._executor, sync_traverse, self._conn, entity_id, max_hops
            )
        except Exception as exc:
            # B5
            log.memory.warning(
                "[memory] kuzu.traverse: failed — returning []",
                exc_info=exc,
                extra={"_fields": {"entity_id": entity_id, "max_hops": max_hops}},
            )
            return []
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.traverse: exit",
            extra={"_fields": {"entity_id": entity_id, "n_results": len(rows)}},
        )
        return rows

    async def health(self) -> HealthReport:
        """Probe Kuzu readiness by running a trivial entity count."""
        # 1. ENTRY
        log.memory.debug("[memory] kuzu.health: entry")
        t0 = time.monotonic()
        loop = asyncio.get_event_loop()
        try:
            entity_count = await loop.run_in_executor(
                self._executor, sync_probe, self._conn
            )
        except Exception as exc:
            # B5
            log.memory.warning(
                "[memory] kuzu.health: probe failed",
                exc_info=exc,
                extra={"_fields": {"data_dir": str(self._data_dir)}},
            )
            return HealthReport(
                name="memory.kuzu",
                status="down",
                details={"error": str(exc), "data_dir": str(self._data_dir)},
                latency_ms=(time.monotonic() - t0) * 1000.0,
            )
        latency_ms = (time.monotonic() - t0) * 1000.0
        report = HealthReport(
            name="memory.kuzu",
            status="ok",
            details={
                "data_dir": str(self._data_dir),
                "entity_count": entity_count,
            },
            latency_ms=latency_ms,
        )
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.health: exit",
            extra={"_fields": dict(report.details, latency_ms=latency_ms)},
        )
        return report

    async def aclose(self) -> None:
        """Shut down the dedicated Kuzu worker thread (no leaked thread).

        Idempotent + fail-safe: called on lifecycle teardown. Waits for any
        in-flight op so the Connection isn't disposed mid-query.
        """
        log.memory.debug("[memory] kuzu.aclose: entry")
        try:
            self._executor.shutdown(wait=True)
        except Exception as exc:
            # B5 — a teardown must never raise.
            log.memory.warning(
                "[memory] kuzu.aclose: executor shutdown failed",
                exc_info=exc,
            )
        log.memory.debug("[memory] kuzu.aclose: exit")

    async def __aenter__(self) -> KuzuAdapter:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
