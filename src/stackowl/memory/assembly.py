"""MemoryAssembly — factory that wires the entire memory subsystem.

Mirrors the ``ProviderRegistry.from_settings()`` / ``OwlRegistry.from_settings()``
pattern: the memory package owns the assembly contract, the startup
orchestrator just calls :meth:`MemoryAssembly.build` and unpacks the result
into :class:`StepServices`.

Per the BMad v2 wiring audit (plan: gleaming-finding-puppy.md, Commit A):

* Hard-fail policy for Kuzu — if the adapter can't initialise we abort the
  gateway phase rather than silently degrade. Operator-approved choice; see
  the plan's "Decision Protocol" vote results.
* DreamWorker is registered via the existing
  ``register_dream_worker_handler`` factory in
  ``stackowl.scheduler.handlers.dream_worker`` (respects the B9 scheduler
  boundary — handlers register via HandlerRegistry, not direct dispatch).
* RolloverSummaryHandler is registered the same way and waits for the
  ``session.rollover`` consumer to enqueue one job per conversation boundary
  (D01.7). It replaced FactExtractionJobHandler, which was registered here, never
  enqueued by anything, and duplicated ``conversation_miner``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.health.contributors import GraphContributor
    from stackowl.learning.lessons_index import LessonsIndex
    from stackowl.memory.dream_worker import DreamWorkerJobHandler
    from stackowl.memory.entity_extractor import EntityExtractor
    from stackowl.memory.fact_promoter import FactPromoter
    from stackowl.memory.kuzu_adapter import KuzuAdapter
    from stackowl.memory.kuzu_sync_handler import KuzuSyncJobHandler
    from stackowl.memory.lancedb_adapter import LanceDBAdapter
    from stackowl.memory.preferences import PreferenceStore
    from stackowl.memory.rollover_summary_handler import RolloverSummaryHandler
    from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
    from stackowl.providers.registry import ProviderRegistry
    from stackowl.tenancy.identity import IdentityResolver


@dataclass(frozen=True)
class MemoryComponents:
    """Frozen container of the wired memory subsystem returned by :meth:`MemoryAssembly.build`.

    Frozen so callers can't mutate the bag — components are owned by whoever
    constructs them (the assembly factory) and consumed read-only by
    :class:`StepServices` and the scheduler.
    """

    bridge: SqliteMemoryBridge
    preference_store: PreferenceStore
    embedding_registry: EmbeddingRegistry
    lancedb: LanceDBAdapter
    # DUR-5 / F069 — None when Kuzu degraded at init (consistent with LanceDB /
    # embeddings degrade-don't-crash policy). classify + kuzu_sync tolerate None.
    kuzu_adapter: KuzuAdapter | None
    promoter: FactPromoter
    entity_extractor: EntityExtractor
    kuzu_sync_handler: KuzuSyncJobHandler
    dream_worker: DreamWorkerJobHandler
    rollover_summary_handler: RolloverSummaryHandler
    lessons_index: LessonsIndex
    # Health surface for the knowledge-graph layer (ok / down).
    graph_health: GraphContributor


class MemoryAssembly:
    """Factory that constructs and wires the complete memory subsystem."""

    @staticmethod
    async def build(
        db: DbPool,
        settings: Settings,
        provider_registry: ProviderRegistry,
        identity_resolver: IdentityResolver | None = None,
        *,
        open_graph: bool = True,
    ) -> MemoryComponents:
        """Construct every memory component and register scheduler handlers.

        ``open_graph`` — whether THIS process should open the embedded Kuzu graph
        DB. Kuzu is a single-writer embedded store, so in the two-process split
        (gateway + core) only ONE process may hold it. The graph is consumed by the
        pipeline (recall/classify) and the background memory jobs, which all run in
        the CORE; the GATEWAY only routes, so it passes ``open_graph=False`` to avoid
        racing the core for the file lock (which made one process degrade to a None
        graph with a spurious ERROR every boot). When False the adapter is None — the
        exact degrade state classify + kuzu_sync already tolerate.
        """
        log.memory.info("[memory] assembly.build: entry")

        # Deferred imports keep this module cheap to import in tests.
        from stackowl.embeddings.registry import EmbeddingRegistry
        from stackowl.memory.entity_extractor import EntityExtractor
        from stackowl.memory.fact_promoter import FactPromoter
        from stackowl.memory.kuzu_adapter import KuzuAdapter
        from stackowl.memory.kuzu_sync_handler import KuzuSyncJobHandler
        from stackowl.memory.lancedb_adapter import LanceDBAdapter
        from stackowl.memory.preferences import PreferenceStore
        from stackowl.memory.rollover_summary_handler import RolloverSummaryHandler
        from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
        from stackowl.paths import StackowlHome
        from stackowl.scheduler.base import HandlerRegistry
        from stackowl.scheduler.handlers.dream_worker import (
            register_dream_worker_handler,
        )

        mem = settings.memory

        # 1a) Embedding registry — already self-heals (sentence-transformer
        # → hash fallback). Health reports 'degraded' when on hash, so the
        # operator can see when semantic search isn't really semantic.
        embedding_registry = await EmbeddingRegistry.create()
        log.memory.info(
            "[memory] assembly: embedding registry ready",
            extra={"_fields": {"semantic": embedding_registry.is_semantic}},
        )

        # 1b) LanceDB adapter — HARD-FAIL per operator choice (Commit B vote).
        # No try/except: if LanceDB can't start, startup must fail so we
        # don't silently lose the vector-recall layer.
        lancedb = LanceDBAdapter(embedding_registry=embedding_registry)
        log.memory.info("[memory] assembly: lancedb adapter ready")

        # 2) Bridge — primary hot-path read/write surface, now with semantic
        # search wired through embeddings + LanceDB.
        bridge = SqliteMemoryBridge(
            db=db,
            embedding_registry=embedding_registry,
            lancedb=lancedb,
            semantic_search_enabled=mem.semantic_search_enabled,
            # MEM-1 (F073) — config-driven blended recall (N + decay half-life).
            recall_limit=mem.recall_limit,
            recall_candidate_pool=mem.recall_candidate_pool,
            recall_decay_half_life_days=mem.recall_decay_half_life_days,
        )
        log.memory.debug("[memory] assembly: bridge ready (semantic enabled)")

        # 3) Persisted preferences store.
        preference_store = PreferenceStore(db=db)
        log.memory.debug("[memory] assembly: preference_store ready")

        # 4) Kuzu adapter — DEGRADE-TO-NONE on init failure (DUR-5 / F069).
        # Consistent with the LanceDB / embedding-registry degrade-don't-crash
        # policy: a missing/broken native Kuzu wheel (e.g. an ARM gap) must NOT
        # abort the whole memory assembly / startup — it degrades the graph
        # layer to a None adapter with a LOUD ERROR and a health-surfaced 'down'
        # status. classify + kuzu_sync already tolerate a None adapter.
        from stackowl.health.contributors import GraphContributor

        # Via the accessor, not a second hand-built path — building it here is
        # what let this diverge from StackowlHome.kuzu_dir() in the first place.
        kuzu_dir = StackowlHome.kuzu_dir()
        kuzu_adapter: KuzuAdapter | None
        if not open_graph:
            # This process (the gateway) must NOT open the single-writer Kuzu DB —
            # the core owns it. Clean, expected None; NOT an error. health surfaces
            # 'not owned by this role' rather than a failure.
            kuzu_adapter = None
            graph_health = GraphContributor(
                available=False, reason="graph owned by core role (not opened here)"
            )
            log.memory.info(
                "[memory] assembly: kuzu adapter not opened in this role "
                "(graph is owned by the core process)",
            )
        else:
            try:
                kuzu_adapter = KuzuAdapter(data_dir=kuzu_dir)
                # ADR-6 Task 3 — thread the live adapter through so graph_health
                # probes the REAL connection (via health()), not just import
                # success. See GraphContributor's docstring for the anti-mistake
                # this closes.
                graph_health = GraphContributor(available=True, adapter=kuzu_adapter)
                log.memory.info(
                    "[memory] assembly: kuzu adapter ready",
                    extra={"_fields": {"data_dir": str(kuzu_dir)}},
                )
            except Exception as exc:
                # B5 / no-hidden-errors — surface LOUDLY, then degrade (don't crash).
                reason = f"{type(exc).__name__}: {exc}"
                kuzu_adapter = None
                graph_health = GraphContributor(available=False, reason=reason)
                log.memory.error(
                    "[memory] assembly: kuzu adapter FAILED to initialise — graph "
                    "layer DEGRADED to None (recall continues without the graph)",
                    exc_info=exc,
                    extra={"_fields": {"data_dir": str(kuzu_dir)}},
                )

        # 5) Consolidation building blocks.
        from stackowl.infra.clock import WallClock

        clock = WallClock()
        promoter = FactPromoter(
            db=db,
            confidence_threshold=mem.promotion_confidence_threshold,
            reinforcement_required=mem.reinforcement_required,
            conversation_fact_reinforcement_required=mem.conversation_fact_reinforcement_required,
            clock=clock,
            settle_minutes=mem.dream_worker_settle_minutes,
            embedding_registry=embedding_registry,
        )
        entity_extractor = EntityExtractor(
            provider_registry=provider_registry,
            sensitive_categories=mem.sensitive_categories,
            preferred_tier="standard",
        )
        kuzu_sync_handler = KuzuSyncJobHandler(
            kuzu_adapter=kuzu_adapter,
            entity_extractor=entity_extractor,
            db=db,
        )

        # 7) FactExtractor + 7a) ConversationMiner — REMOVED (D08.1).
        #
        # These built the write path that produced 88,631 facts, 37.1% of which
        # mentioned a trace id or failure_class: the extractor took session_key
        # and never consulted it, so every incident-recovery and retry
        # conversation was mined as though a human had said it. Curated memory
        # (memory/curated.py) is the replacement — two files, a hard budget, and
        # the agent doing its own forgetting.

        # 6) DreamWorker — register via existing factory (respects B9 boundary).
        dream_worker = register_dream_worker_handler()
        # NOT SCHEDULED (D08.1 / N01), and now EMPTY as well (D08.2 seam 3). The
        # handler stays REGISTERED because it is the seat for N01 "Dreaming";
        # all five of its phases were fact work and went with the extraction
        # pipeline, so it takes no arguments any more and reports honestly that
        # it has nothing to run. N01 gives it phases; the wiring is already here.
        #
        # A job waking every 30 minutes to do nothing is worse than no job: it
        # writes successful run records that read as a healthy loop, which is the
        # precise failure ADR-19 exists to kill. Migration 0113 disables the row
        # that this call used to seed. Re-scheduling is one INSERT when N01 has
        # its first real phase.

        # 7b) RolloverSummaryHandler — the conversation BOUNDARY's memory work
        # (D01.7). Enqueued per boundary by the session.rollover consumer, which
        # the orchestrator subscribes because that is where the EventBus lives.
        #
        # This REPLACES FactExtractionJobHandler, which was registered here and
        # never enqueued by anything. It no longer takes a miner: the
        # extraction pipeline it used to nudge is retired (D08.1), and what
        # survives here is the narrative artifact, which was never duplicated.
        rollover_summary_handler = RolloverSummaryHandler(
            db=db,
            bridge=bridge,
            provider_registry=provider_registry,
        )
        HandlerRegistry.instance().register(rollover_summary_handler)
        log.memory.info(
            "[memory] assembly: rollover_summary handler registered",
            extra={"_fields": {"handler": rollover_summary_handler.handler_name}},
        )

        # Learning Commit 5 — LessonsIndex over the LanceDB "lessons" table.
        # Shares the embedding registry; subsystems publish into it as they
        # produce learning artifacts (reflections, skills, tool heuristics).
        from stackowl.learning.lessons_index import LessonsIndex
        from stackowl.learning.lessons_lance import LessonsLanceAdapter

        lessons_adapter = LessonsLanceAdapter()
        lessons_index = LessonsIndex(
            adapter=lessons_adapter,
            embedding_registry=embedding_registry,
        )
        log.memory.info("[memory] assembly: lessons_index ready")

        log.memory.info("[memory] assembly.build: exit — all components wired")
        return MemoryComponents(
            bridge=bridge,
            preference_store=preference_store,
            embedding_registry=embedding_registry,
            lancedb=lancedb,
            kuzu_adapter=kuzu_adapter,
            promoter=promoter,
            entity_extractor=entity_extractor,
            lessons_index=lessons_index,
            kuzu_sync_handler=kuzu_sync_handler,
            dream_worker=dream_worker,
            rollover_summary_handler=rollover_summary_handler,
            graph_health=graph_health,
        )
