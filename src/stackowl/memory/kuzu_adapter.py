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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.memory.bridge import HealthReport
from stackowl.memory.kuzu_helpers import (
    sync_create_schema,
    sync_delete_skill,
    sync_delete_trait,
    sync_link_entities,
    sync_link_fact_to_entity,
    sync_link_owl_has_trait,
    sync_link_owl_owns_skill,
    sync_list_skill_ids,
    sync_list_trait_ids,
    sync_probe,
    sync_traverse,
    sync_upsert_entity,
    sync_upsert_fact,
    sync_upsert_owl,
    sync_upsert_skill,
    sync_upsert_trait,
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
        # ADR-6 F-87 (Task 3, self-heal) — cached construct-failure reason, mirrors
        # LanceDBAdapter/DbPool. Set by `_construct_db_and_conn`'s caller on
        # failure, cleared on success; read by `available`/`unavailable_reason`.
        self._unavailable_reason: str | None = None
        # Create the Database + Connection ON the worker thread (Kuzu may pin a
        # Connection to its creating thread) and bootstrap the schema there too,
        # so the very first access is already on the confined thread. Shared with
        # `ensure_available()` via `_construct_db_and_conn` so both paths build
        # the handles identically (DRY).
        self._db: kuzu.Database
        self._conn: kuzu.Connection
        self._db, self._conn = self._executor.submit(
            self._construct_db_and_conn
        ).result()
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.init: exit",
            extra={"_fields": {"data_dir": str(self._data_dir)}},
        )

    def _construct_db_and_conn(self) -> tuple[kuzu.Database, kuzu.Connection]:
        """Build a fresh Database + bootstrapped Connection.

        MUST run on the confined F067 worker thread — callers submit this to
        ``self._executor``, never call it directly. Shared by ``__init__`` and
        ``ensure_available()`` so both construction paths are identical (DRY).
        """
        db = self._kuzu_mod.Database(str(self._db_path))
        conn = self._kuzu_mod.Connection(db)
        try:
            sync_create_schema(conn)
        except Exception as exc:
            # B5 — a failed bootstrap must surface (hard-fail per assembly policy).
            log.memory.error(
                "[memory] kuzu._construct_db_and_conn: schema bootstrap failed",
                exc_info=exc,
                extra={"_fields": {"data_dir": str(self._data_dir)}},
            )
            raise
        return db, conn

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

    async def upsert_owl_node(self, name: str) -> None:
        """Upsert an Owl node keyed by ``name``."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.upsert_owl_node: entry", extra={"_fields": {"name": name}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.upsert_owl_node")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(self._executor, sync_upsert_owl, self._conn, name)
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.upsert_owl_node: exit", extra={"_fields": {"name": name}},
        )

    async def upsert_skill_node(self, skill_id: str, owner_id: str, name: str) -> None:
        """Upsert a Skill node keyed by ``skill_id`` (``f"{owner_id}::{name}"``)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.upsert_skill_node: entry",
            extra={"_fields": {"skill_id": skill_id, "name": name}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.upsert_skill_node")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(
            self._executor, sync_upsert_skill, self._conn, skill_id, owner_id, name,
        )
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.upsert_skill_node: exit", extra={"_fields": {"skill_id": skill_id}},
        )

    async def upsert_trait_node(
        self, trait_id: str, owl_name: str, trait_name: str, value: float,
    ) -> None:
        """Upsert a Trait node keyed by ``trait_id`` (``f"{owl_name}::{trait_name}"``)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.upsert_trait_node: entry",
            extra={"_fields": {"trait_id": trait_id, "value": value}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.upsert_trait_node")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(
            self._executor, sync_upsert_trait, self._conn, trait_id, owl_name, trait_name, value,
        )
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.upsert_trait_node: exit", extra={"_fields": {"trait_id": trait_id}},
        )

    async def link_owl_owns_skill(self, owl_name: str, skill_id: str) -> None:
        """Create an Owl -> Skill OWNS edge (idempotent)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.link_owl_owns_skill: entry",
            extra={"_fields": {"owl_name": owl_name, "skill_id": skill_id}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.link_owl_owns_skill")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(
            self._executor, sync_link_owl_owns_skill, self._conn, owl_name, skill_id,
        )
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.link_owl_owns_skill: exit",
            extra={"_fields": {"owl_name": owl_name, "skill_id": skill_id}},
        )

    async def link_owl_has_trait(self, owl_name: str, trait_id: str) -> None:
        """Create an Owl -> Trait HAS_TRAIT edge (idempotent)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.link_owl_has_trait: entry",
            extra={"_fields": {"owl_name": owl_name, "trait_id": trait_id}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.link_owl_has_trait")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(
            self._executor, sync_link_owl_has_trait, self._conn, owl_name, trait_id,
        )
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.link_owl_has_trait: exit",
            extra={"_fields": {"owl_name": owl_name, "trait_id": trait_id}},
        )

    async def delete_skill_node(self, skill_id: str) -> None:
        """Remove a Skill node and its edges (reconciliation prune)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.delete_skill_node: entry", extra={"_fields": {"skill_id": skill_id}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.delete_skill_node")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(self._executor, sync_delete_skill, self._conn, skill_id)
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.delete_skill_node: exit", extra={"_fields": {"skill_id": skill_id}},
        )

    async def delete_trait_node(self, trait_id: str) -> None:
        """Remove a Trait node and its edges (reconciliation prune)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.delete_trait_node: entry", extra={"_fields": {"trait_id": trait_id}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.delete_trait_node")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(self._executor, sync_delete_trait, self._conn, trait_id)
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.delete_trait_node: exit", extra={"_fields": {"trait_id": trait_id}},
        )

    async def list_skill_ids(self) -> list[str]:
        """All Skill node ids currently in the graph (reconciliation diffing)."""
        # 1. ENTRY
        log.memory.debug("[memory] kuzu.list_skill_ids: entry")
        TestModeGuard.assert_not_test_mode("kuzu.list_skill_ids")
        loop = asyncio.get_event_loop()
        # 3. STEP
        ids = await loop.run_in_executor(self._executor, sync_list_skill_ids, self._conn)
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.list_skill_ids: exit", extra={"_fields": {"count": len(ids)}},
        )
        return ids

    async def list_trait_ids(self) -> list[str]:
        """All Trait node ids currently in the graph (reconciliation diffing)."""
        # 1. ENTRY
        log.memory.debug("[memory] kuzu.list_trait_ids: entry")
        TestModeGuard.assert_not_test_mode("kuzu.list_trait_ids")
        loop = asyncio.get_event_loop()
        # 3. STEP
        ids = await loop.run_in_executor(self._executor, sync_list_trait_ids, self._conn)
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.list_trait_ids: exit", extra={"_fields": {"count": len(ids)}},
        )
        return ids

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

    # ----- HealableResource protocol (ADR-6 F-87, Task 3) -----------------------
    # `available`/`unavailable_reason` are cached reads of construct state (set by
    # `ensure_available()`/`__init__`), not a fresh probe per access — mirrors
    # LanceDBAdapter/DbPool. A caller that wants a truthful up-to-date verdict
    # should go through `health()`, which always re-probes.

    @property
    def available(self) -> bool:
        return self._conn is not None and self._db is not None

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    async def ensure_available(self) -> None:
        """Tear down and reconstruct the Database/Connection; raises if unrecoverable.

        F067 — the ENTIRE teardown-and-reconstruct runs on the confined single
        Kuzu worker thread via ``run_in_executor(self._executor, ...)``, never on
        whatever thread/task calls this method, so the non-thread-safe Connection
        is never touched from two threads. Unconditionally replaces the handles
        rather than no-op'ing when they look set — callers
        (``retry_once_on_dead_handle``, the health sweep's RecoveryActuator) only
        invoke this after a dead-handle/down signal, so a possibly-wedged handle
        is never trusted. Lets failure propagate — caller owns retry/backoff.
        """
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.ensure_available: entry",
            extra={"_fields": {"had_conn": self._conn is not None}},
        )
        loop = asyncio.get_event_loop()
        try:
            # 3. STEP — teardown + reconstruct, confined to the single kuzu thread
            self._db, self._conn = await loop.run_in_executor(
                self._executor, self._teardown_and_reconstruct
            )
        except Exception as exc:
            # The old handles were closed by `_teardown_and_reconstruct` before the
            # failure (or never survived it) — never leave a stale/closed handle
            # behind for `available` to mistake as live.
            self._conn = None  # type: ignore[assignment]
            self._db = None  # type: ignore[assignment]
            self._unavailable_reason = f"{type(exc).__name__}: {exc}"
            log.memory.error(
                "[memory] kuzu.ensure_available: reconstruct failed",
                exc_info=exc,
                extra={"_fields": {"data_dir": str(self._data_dir)}},
            )
            raise
        self._unavailable_reason = None
        # 4. EXIT
        log.memory.info(
            "[memory] kuzu.ensure_available: exit — reconstructed",
            extra={"_fields": {"data_dir": str(self._data_dir)}},
        )

    def _teardown_and_reconstruct(self) -> tuple[kuzu.Database, kuzu.Connection]:
        """Close the old handles (best-effort) and build fresh ones.

        MUST run on the confined F067 worker thread — only called via
        ``run_in_executor(self._executor, ...)`` from ``ensure_available()``.
        """
        try:
            self._conn.close()
        except Exception as exc:
            # B5 — a dead connection may already be unclosable; never block
            # reconstruction on a failed teardown of the handle we're discarding.
            log.memory.warning(
                "[memory] kuzu._teardown_and_reconstruct: conn.close() failed",
                exc_info=exc,
            )
        try:
            self._db.close()
        except Exception as exc:
            log.memory.warning(
                "[memory] kuzu._teardown_and_reconstruct: db.close() failed",
                exc_info=exc,
            )
        return self._construct_db_and_conn()

    def register_on_recycled(self, cb: Callable[[], None]) -> None:
        """No-op: callers always fetch the live conn/db via ``self._conn``/``self._db``.

        No downstream code caches those handles directly — every op reads them
        fresh at call time — so a reconnect is transparent to every caller.
        Mirrors ``LanceDBAdapter.register_on_recycled``.
        """
        log.memory.debug(
            "[memory] kuzu.register_on_recycled: no-op (no downstream dependents)"
        )

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
