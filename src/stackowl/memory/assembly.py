"""MemoryAssembly — factory that wires the entire memory subsystem.

Mirrors the ``ProviderRegistry.from_settings()`` / ``OwlRegistry.from_settings()``
pattern: the memory package owns the assembly contract, the startup
orchestrator just calls :meth:`MemoryAssembly.build` and unpacks the result
into :class:`StepServices`.

Per the BMad v2 wiring audit (plan: gleaming-finding-puppy.md, Commit A):

* Hard-fail policy for Kuzu — if the adapter can't initialise we abort the
  gateway phase rather than silently degrade. Operator-approved choice; see
  the plan's "Decision Protocol" vote results.
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
    from stackowl.memory.kuzu_adapter import KuzuAdapter
    from stackowl.memory.preferences import PreferenceStore
    from stackowl.memory.providers import MemoryProviderRegistry
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
    # DUR-5 / F069 — None when Kuzu degraded at init (consistent with LanceDB /
    # embeddings degrade-don't-crash policy). classify and graph_reconciliation
    # both tolerate None.
    kuzu_adapter: KuzuAdapter | None
    rollover_summary_handler: RolloverSummaryHandler
    lessons_index: LessonsIndex
    #: D08.2 slice C — the frozen active memory-provider set for this incarnation.
    #: NOT named `provider_registry`: that name is already taken in this module by
    #: the AI ProviderRegistry, and shadowing it here cost a mypy error and would
    #: have been a trap for the next reader.
    memory_providers: MemoryProviderRegistry
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
        exact degrade state classify and graph_reconciliation already tolerate.
        """
        log.memory.info("[memory] assembly.build: entry")

        # Deferred imports keep this module cheap to import in tests.
        from stackowl.embeddings.registry import EmbeddingRegistry
        from stackowl.memory.kuzu_adapter import KuzuAdapter
        from stackowl.memory.preferences import PreferenceStore
        from stackowl.memory.rollover_summary_handler import RolloverSummaryHandler
        from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
        from stackowl.paths import StackowlHome
        from stackowl.scheduler.base import HandlerRegistry

        mem = settings.memory

        # 1a) Embedding registry — already self-heals (sentence-transformer
        # → hash fallback). Health reports 'degraded' when on hash, so the
        # operator can see when semantic search isn't really semantic.
        embedding_registry = await EmbeddingRegistry.create()
        log.memory.info(
            "[memory] assembly: embedding registry ready",
            extra={"_fields": {"semantic": embedding_registry.is_semantic}},
        )

        # The LanceDB adapter was constructed here and HARD-FAILED startup if it
        # could not start (Commit B operator vote), because recall depended on it.
        # D08.2 removed it: the vectors it served hydrated from committed_facts,
        # which has 0 rows and no writer since seam 3 pass 4.

        # 2) Bridge — primary hot-path read/write surface. Recall is FTS5 over
        # committed_facts; the semantic half went with LanceDB in D08.2.
        #
        # `mem.semantic_search_enabled` is NOT passed here any more: the bridge has
        # nothing left to gate with it. The key itself is very much alive — ESC-7
        # repointed it at LESSONS recall (see the LessonsIndex construction below),
        # which is the one place embeddings still rank anything. It was repointed
        # rather than deleted because MemorySettings is `extra="forbid"`, so
        # removing a key a deployment has set turns a no-op toggle into a hard boot
        # failure.
        bridge = SqliteMemoryBridge(
            db=db,
            embedding_registry=embedding_registry,
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
        # status. classify and graph_reconciliation already tolerate a None adapter.
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
        # The WallClock that stood here existed ONLY to give FactPromoter its settle
        # window, and went with it in D08.2 seam 3 pass 4 — removing a writer orphans
        # whatever was feeding it.
        # 5) Consolidation building blocks — REMOVED 2026-09-01 (Bakir: "whatever
        # retired should be deleted"). EntityExtractor existed only to feed
        # KuzuSyncJobHandler, and that handler joins ON committed_facts, which
        # D08.1's migration 0112 retired to zero rows. Measured before deleting:
        # NO job row for it at all, so it was constructed on every boot and never
        # ran. The Kuzu ADAPTER stays — owls/evolution.py and pipeline/steps/
        # classify.py query the graph, which is why D08.1 kept it.

        # 7) FactExtractor + 7a) ConversationMiner — REMOVED (D08.1).
        #
        # These built the write path that produced 88,631 facts, 37.1% of which
        # mentioned a trace id or failure_class: the extractor took session_key
        # and never consulted it, so every incident-recovery and retry
        # conversation was mined as though a human had said it. Curated memory
        # (memory/curated.py) is the replacement — two files, a hard budget, and
        # the agent doing its own forgetting.

        # 6) DreamWorker — DELETED 2026-09-01 (Bakir: "whatever retired should be
        # deleted from code and we should never have dead code"). It was
        # registered, unscheduled, and EMPTY: all five phases were fact work and
        # went with the extraction pipeline in D08.2, and its job row
        # (dream-50fd10ab) has been enabled=0 since migration 0113, last run
        # 2026-08-11. It was kept as "the seat for N01 Dreaming" — but a seat is
        # not a capability, and it was twice misread as live while diagnosing the
        # conversation-memory gap. Git history holds the class; N01 builds its own.

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
        from stackowl.learning.lessons_store import SqliteLessonsStore

        # D08.2 — the lessons corpus lives in SQLite now, ranked by a numpy scan.
        # LanceDB was 236MB of dependency (with pyarrow) for a 5.4MB corpus, and
        # brute force over 3,680 x 384 is one matmul. See learning/lessons_store.py.
        # The MODEL is passed, not defaulted. `embedding_model` defaults to "" and
        # only cli/app.py was supplying it, so every lesson written in production
        # recorded '' — all 5,146 of them. That makes the dedup gate's semantic
        # rung INERT for the largest store, because the gate refuses to compare two
        # vectors unless their model matches and is non-empty (reflections mix
        # all-MiniLM-L6-v2 with the degraded hash-v1-384d fallback, both 384-dim,
        # so the arithmetic succeeds and the answer is meaningless). Lessons already
        # dedupe EXACTLY via the upsert on a deterministic lesson_id, so rung 3 is
        # the only rung that can see their 1,153 near-duplicates at cosine >= 0.90.
        lessons_adapter = SqliteLessonsStore(
            db, embedding_model=embedding_registry.active_model
        )
        # ESC-7 — the flag now gates LESSONS recall, the one place embeddings
        # still rank anything. It gates reads only; see LessonsIndex.__init__.
        lessons_index = LessonsIndex(
            adapter=lessons_adapter,
            embedding_registry=embedding_registry,
            semantic_search_enabled=mem.semantic_search_enabled,
        )
        log.memory.info("[memory] assembly: lessons_index ready")

        # D08.2 slice C — memory providers. Resolved HERE and nowhere else:
        # assembly runs once per incarnation, which is exactly the freeze Law 1
        # needs. Any provider a plugin registered is judged against the ceiling
        # now; the built-in is always present and never counted.
        from stackowl.memory.providers import MemoryProviderRegistry

        memory_providers = MemoryProviderRegistry(
            ceiling=mem.provider_schema_ceiling
        )
        memory_providers.resolve()

        log.memory.info("[memory] assembly.build: exit — all components wired")
        return MemoryComponents(
            bridge=bridge,
            preference_store=preference_store,
            embedding_registry=embedding_registry,
            kuzu_adapter=kuzu_adapter,
            lessons_index=lessons_index,
            memory_providers=memory_providers,
            rollover_summary_handler=rollover_summary_handler,
            graph_health=graph_health,
        )
