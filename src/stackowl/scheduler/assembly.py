"""SchedulerAssembly — wires JobScheduler, Supervisor, and the orphaned handlers.

Mirrors :class:`MemoryAssembly` / :class:`NotificationAssembly` /
:class:`TuiAssembly`. The scheduler package owns its own assembly contract;
the startup orchestrator calls :meth:`SchedulerAssembly.build` once and
threads the supervisor into the gateway lifecycle.

Per the wiring plan (gleaming-finding-puppy.md, Commit E):

* Without :class:`JobScheduler` running under a :class:`Supervisor`, NO
  registered scheduler handlers actually dispatch. This is the critical
  gap closed by this commit — browser handlers, dream worker, fact
  extraction, notification digest, etc. all needed the scheduler loop.
* Six previously-orphaned handlers (morning_brief, evolution,
  knowledge_prune, check_in, tool_pruning, goal_execution) are
  registered here. Three are auto-scheduled per operator vote;
  three are register-only (enqueued on demand).
* Each handler's heavy imports are deferred so the assembly module
  stays cheap when scheduler is disabled.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.channels.liveness import ChannelLivenessStore
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.events.bus import EventBus
    from stackowl.health.aggregator import HealthAggregator
    from stackowl.health.contributors import GraphContributor
    from stackowl.infra.resilience import HealableResource
    from stackowl.memory.assembly import MemoryComponents
    from stackowl.memory.lancedb_adapter import LanceDBAdapter
    from stackowl.memory.reflection_writer_handler import ReflectionWriterHandler
    from stackowl.notifications.deliverer import ProactiveDeliverer
    from stackowl.objectives.driver import ObjectiveDriverHandler
    from stackowl.owls.concurrency import ConcurrencyGovernor
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.backends.base import OrchestratorBackend
    from stackowl.providers.registry import ProviderRegistry
    from stackowl.scheduler.handlers.check_in import CheckInHandler
    from stackowl.scheduler.handlers.goal_execution import GoalExecutionHandler
    from stackowl.scheduler.handlers.health_sweep import HealthSweepHandler
    from stackowl.scheduler.handlers.incident_escalation import (
        IncidentEscalationHandler,
    )
    from stackowl.scheduler.handlers.knowledge_prune import KnowledgePruneHandler
    from stackowl.scheduler.handlers.morning_brief import MorningBriefHandler
    from stackowl.scheduler.handlers.tool_outcome_miner_handler import (
        ToolOutcomeMinerHandler,
    )
    from stackowl.scheduler.handlers.tool_pruning import ToolPruningHandler
    from stackowl.scheduler.scheduler import JobScheduler
    from stackowl.skills.assembly import SkillsComponents
    from stackowl.skills.synthesizer_handler import SkillSynthesizerHandler
    from stackowl.supervisor.supervisor import Supervisor
    from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry


_INSERT_JOB_SQL = """
INSERT INTO jobs
    (job_id, handler_name, schedule, idempotency_key, last_run_at,
     next_run_at, status, retry_count, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_SELECT_EXISTING_SQL = "SELECT job_id FROM jobs WHERE handler_name = ?"
# job_runs.job_id REFERENCES jobs(job_id) with no ON DELETE CASCADE (migration
# 0009) — a retired job that ever ran leaves history rows that must go first,
# or the jobs delete below trips a FOREIGN KEY constraint failure.
_DELETE_RETIRED_JOB_RUNS_SQL = (
    "DELETE FROM job_runs WHERE job_id IN "
    "(SELECT job_id FROM jobs WHERE handler_name = ?)"
)
_DELETE_RETIRED_JOB_SQL = "DELETE FROM jobs WHERE handler_name = ?"


@dataclass(frozen=True)
class SchedulerComponents:
    """Frozen container for the wired scheduler subsystem."""

    scheduler: JobScheduler
    supervisor: Supervisor
    morning_brief_handler: MorningBriefHandler
    check_in_handler: CheckInHandler
    knowledge_prune_handler: KnowledgePruneHandler
    tool_pruning_handler: ToolPruningHandler
    goal_execution_handler: GoalExecutionHandler
    objective_driver_handler: ObjectiveDriverHandler
    reflection_writer_handler: ReflectionWriterHandler
    skill_synthesizer_handler: SkillSynthesizerHandler
    tool_outcome_miner_handler: ToolOutcomeMinerHandler
    health_sweep_handler: HealthSweepHandler
    incident_escalation_handler: IncidentEscalationHandler


class SchedulerAssembly:
    """Factory that wires JobScheduler + Supervisor + the 6 orphaned handlers."""

    @staticmethod
    async def build(
        db: DbPool,
        settings: Settings,
        event_bus: EventBus,
        provider_registry: ProviderRegistry,
        owl_registry: OwlRegistry,
        memory_components: MemoryComponents,
        backend: OrchestratorBackend,
        skills_components: SkillsComponents,
        proactive_deliverer: ProactiveDeliverer | None = None,
        delegation_governor: ConcurrencyGovernor | None = None,
        turn_registry: object | None = None,
        browser_runtime: HealableResource | None = None,
        mcp_client: object | None = None,  # McpClient — TYPE_CHECKING import would be circular
        # Task 4 — threaded into SkillSynthesizerHandler so its gated skill-authoring
        # writes have a real ConsequentialActionGate to consult instead of always
        # failing closed on a None gate. None here reproduces that fail-closed default.
        consent_gate: ConsequentialActionGate | None = None,
        # Task 7 — the live ToolRegistry, so IncidentEscalationHandler's
        # "alternative" verdict consumer can consult
        # capability_substitution.find_substitute (a PURE, read-only decision
        # function — no execution) to confirm a live sibling capability exists.
        # None → that consult honestly reports "no registry wired" (logged).
        tool_registry: ToolRegistry | None = None,
    ) -> SchedulerComponents:
        log.scheduler.info("[scheduler] assembly.build: entry")

        # Deferred imports — keep this module cheap when scheduler isn't used.
        from stackowl.scheduler.base import HandlerRegistry
        from stackowl.scheduler.handlers.check_in import CheckInHandler
        from stackowl.scheduler.handlers.downloads_janitor import (
            register_downloads_janitor_handler,
        )
        from stackowl.scheduler.handlers.evolution import register_evolution_handler
        from stackowl.scheduler.handlers.goal_execution import GoalExecutionHandler
        from stackowl.scheduler.handlers.knowledge_prune import KnowledgePruneHandler
        from stackowl.scheduler.handlers.morning_brief import MorningBriefHandler
        from stackowl.scheduler.handlers.tool_pruning import ToolPruningHandler
        from stackowl.scheduler.scheduler import JobScheduler
        from stackowl.supervisor.supervisor import Supervisor

        # 1) JobScheduler — the polling loop that dispatches due jobs. Threaded with
        # the user IANA tz so a daily@HH:MM job re-arms at the right LOCAL instant
        # and shares the quiet-hours clock (F108).
        scheduler = JobScheduler(
            db=db,
            tz=settings.system.timezone or "UTC",
            turn_registry=turn_registry,
        )
        log.scheduler.debug("[scheduler] assembly: JobScheduler constructed")

        # 2) Supervisor — owns the scheduler's runtime task.
        supervisor = Supervisor()
        supervisor.register(scheduler)
        log.scheduler.debug("[scheduler] assembly: Supervisor wraps JobScheduler")

        # 3) Register the 6 orphaned handlers. Each uses HandlerRegistry directly
        # OR an existing factory (evolution uses register_evolution_handler).

        # C1/F101+F102 — the exactly-once delivery ledger, shared by every
        # cron-born proactive handler so an event-driven send and the cron send for
        # the same occurrence deliver once. Built once here next to the deliverer.
        from stackowl.notifications.delivery_ledger import DeliveryLedger

        delivery_ledger = DeliveryLedger(db=db)

        morning_brief_handler = MorningBriefHandler(
            memory_bridge=memory_components.bridge,
            scheduler=scheduler,
            db=db,
            event_bus=event_bus,
            settings=settings,
            proactive_deliverer=proactive_deliverer,
            delivery_ledger=delivery_ledger,
        )
        HandlerRegistry.instance().register(morning_brief_handler)

        check_in_handler = CheckInHandler(
            memory_bridge=memory_components.bridge,
            scheduler=scheduler,
            db=db,
            settings=settings,
            proactive_deliverer=proactive_deliverer,
            delivery_ledger=delivery_ledger,
        )
        HandlerRegistry.instance().register(check_in_handler)

        knowledge_prune_handler = KnowledgePruneHandler(pruner=memory_components.pruner)
        HandlerRegistry.instance().register(knowledge_prune_handler)

        # Downloads janitor — needs no browser runtime/services, so it registers
        # directly here (its own factory defaults to StackowlHome.downloads_dir()).
        register_downloads_janitor_handler()

        tool_pruning_handler = ToolPruningHandler()
        HandlerRegistry.instance().register(tool_pruning_handler)

        # WS-B/C1 — share the SAME delivery seam morning_brief/check_in use so a
        # user-created goal's answer is routed back to the chat it was scheduled
        # from, exactly-once. Wired only when a real deliverer exists; absent it,
        # goal_execution records results without a send (never a fake "delivered").
        goal_job_deliverer = None
        if proactive_deliverer is not None:
            from stackowl.notifications.proactive_job import ProactiveJobDeliverer

            goal_job_deliverer = ProactiveJobDeliverer(
                proactive_deliverer, delivery_ledger, settings=settings
            )

        # PB-CANARY — ONE ChannelLivenessStore instance shared by PB0b's receive
        # contributor, the new send-path canary contributor, AND this handler's
        # writer (same channel-agnostic channel_liveness table; only the
        # registration site names a channel). Built once here — NOT inside
        # _build_health_aggregator — so the handler can write to the identical
        # store the health sweep reads from. Gated on bot_token exactly like the
        # existing receive contributor: no telegram configured => no signal to
        # produce or consume.
        liveness_store: ChannelLivenessStore | None = None
        if settings.telegram_channel.bot_token:
            from stackowl.channels.liveness import ChannelLivenessStore
            from stackowl.db.pool import DbPool, default_db_path
            from stackowl.infra.clock import WallClock

            liveness_store = ChannelLivenessStore(DbPool(default_db_path()), WallClock())

        from stackowl.scheduler.handlers.telegram_canary import TelegramCanaryHandler

        telegram_canary_handler = TelegramCanaryHandler(
            job_deliverer=goal_job_deliverer, liveness_store=liveness_store,
        )
        HandlerRegistry.instance().register(telegram_canary_handler)

        # F-61 (S2): give the scheduler the same delivery seam so a retry-exhausted
        # job failure is audited AND surfaces a proactive operator notification,
        # not just an ERROR log line. Wired post-construction because the deliverer
        # depends on the ledger, which is built after the JobScheduler.
        scheduler._job_deliverer = goal_job_deliverer

        goal_execution_handler = GoalExecutionHandler(
            backend=backend,
            db=db,
            settings=settings,
            job_deliverer=goal_job_deliverer,
        )
        HandlerRegistry.instance().register(goal_execution_handler)

        # Objective Manager (keystone) — the ObjectiveDriver advances every active
        # standing objective by one sub-goal per tick through the SAME pipeline
        # backend + delivery seam as goal_execution. Seeded below ("every 1m").
        from stackowl.objectives.driver import ObjectiveDriverHandler

        objective_driver_handler = ObjectiveDriverHandler(
            db=db,
            backend=backend,
            settings=settings,
            job_deliverer=goal_job_deliverer,
            # For the OPTIONAL post-hoc LLM acceptance layer (flag-OFF default).
            provider_registry=provider_registry,
        )
        HandlerRegistry.instance().register(objective_driver_handler)

        # Anticipation (Phase 3) — the perch watches a filesystem path and pings on
        # change through the SAME durable delivery seam. on_demand (created via the
        # cronjob `watch` action with watch_path); no standing seed. Needs no
        # browser/db — just a state dir + the deliverer.
        from stackowl.paths import StackowlHome
        from stackowl.scheduler.handlers.perch import register_perch_handler

        register_perch_handler(StackowlHome.home() / "perch", goal_job_deliverer)

        # Evolution uses its own register factory (which owns the import + DI).
        register_evolution_handler(
            db=db,
            provider_registry=provider_registry,
            owl_registry=owl_registry,
            # PARL-7 (F084) — share the host-wide in-flight governor so the
            # nightly evolution batch's concurrent fan-out draws from the SAME
            # budget as delegation/parliament.
            delegation_governor=delegation_governor,
        )

        # FR-4 (learning-loop consolidation) — ReflectionWriterHandler now scores
        # AND reflects in one execute() call (every 15 min). The standalone
        # critic_scorer job (was every 10 min) is gone: ReflectionWriterHandler
        # composes a CriticScorerHandler internally and calls its execute() first,
        # so one scheduler job replaces two and a fresh outcome can be
        # scored-then-reflected in the SAME run instead of waiting on two
        # cadences. Uses the MemoryAssembly's embedding registry so reflections
        # can be retrieved semantically by classify.py.
        from stackowl.memory.reflection_writer_handler import ReflectionWriterHandler

        reflection_writer_handler = ReflectionWriterHandler(
            db=db,
            provider_registry=provider_registry,
            embedding_registry=memory_components.embedding_registry,
            lessons_index=memory_components.lessons_index,
            # Preserves the standalone critic_scorer job's original
            # defer-under-load behavior (heavy LLM batch yields to live
            # turns) now that it's composed inside this handler instead of
            # being separately scheduled/deferred.
            turn_registry=turn_registry,
        )
        HandlerRegistry.instance().register(reflection_writer_handler)
        # FR-4 cleanup — an already-running install may have a critic_scorer
        # row seeded from before this consolidation (it was a standalone
        # every-10m job). No handler claims it anymore, so it would otherwise
        # be claimed→released on every poll forever, logging a misleading
        # "handler not registered" warning. Idempotent: no-op once removed.
        # job_runs history must go first (FK, no cascade) or the delete below
        # trips a FOREIGN KEY constraint failure on any install where the
        # every-10m job actually ran before being retired.
        await db.execute(_DELETE_RETIRED_JOB_RUNS_SQL, ("critic_scorer",))
        await db.execute(_DELETE_RETIRED_JOB_SQL, ("critic_scorer",))

        # Learning Commit 3 sub-phase 3c — SkillSynthesizerHandler runs daily
        # against accumulated task_outcomes + reflections + existing learned
        # skills. Discovers new tactic clusters → writes learned/<name>/SKILL.md.
        # Refines mid-tier learned skills. Deprecates low-performers into
        # learned/_deprecated/. All writes audited with actor='agent:synthesizer'.
        from stackowl.paths import StackowlHome
        from stackowl.skills.synthesizer_handler import SkillSynthesizerHandler

        skill_synthesizer_handler = SkillSynthesizerHandler(
            db=db,
            provider_registry=provider_registry,
            skill_store=skills_components.store,
            skills_root=StackowlHome.skills_dir(),
            embedding_registry=memory_components.embedding_registry,
            owl_registry=owl_registry,
            consent_gate=consent_gate,
        )
        HandlerRegistry.instance().register(skill_synthesizer_handler)

        # Learning Commit 5 — ToolOutcomeMinerHandler scans task_outcomes
        # daily, mines (tool, condition → outcome) heuristics, publishes
        # summaries into the LessonsIndex.
        from stackowl.scheduler.handlers.tool_outcome_miner_handler import (
            ToolOutcomeMinerHandler,
        )

        tool_outcome_miner_handler = ToolOutcomeMinerHandler(
            db=db,
            lessons_index=memory_components.lessons_index,
        )
        HandlerRegistry.instance().register(tool_outcome_miner_handler)

        # F-87 — periodic in-process HEALTH SWEEP. Health was detect-only / on
        # demand (only the out-of-process `stackowl health` CLI ran the
        # aggregator); nothing inside the running service ever noticed a subsystem
        # going down. Build a live aggregator from the LOCAL, in-process-safe
        # contributors (db / filesystem / graph / enabled providers — the same set
        # the CLI uses, minus Browser/Resilience which need live-runtime refs) and
        # register a handler that collects on a cadence and alerts on down/degraded.
        health_aggregator = _build_health_aggregator(
            settings,
            liveness_store,
            memory_components.embedding_registry,
            memory_components.lancedb,
            memory_components.graph_health,
        )
        health_alert = _build_health_alert_sink(proactive_deliverer, settings)
        from stackowl.scheduler.handlers.health_sweep import HealthSweepHandler

        # ADR-6 F-87 — close the loop: hand the live serve-process HealableResources
        # (DbPool, each provider) to the sweep keyed by their health-status name, so
        # a down subsystem is RECYCLED + re-verified, not just alerted. Heal is
        # flag-gated in the handler (settings.health_loop), so this map is consulted
        # ONLY when ON — flag OFF stays byte-identical regardless of what's wired.
        healers: dict[str, HealableResource] = {
            "db": db,
            "embedding_registry": memory_components.embedding_registry,
            "lancedb": memory_components.lancedb,
        }
        # Task 9 — durable-task liveness watchdog. B4 crash-recovery only reaps
        # orphaned tasks at BOOT; a task whose background drive died mid-execution
        # while the server kept running stayed stuck 'running' until the next
        # restart. TaskLivenessSweepHandler is BOTH a recurring JobHandler (seeded
        # below) AND, like the channel adapters above, the SAME object doubles as
        # the HealableResource/HealthContributor pair — keyed by its own
        # contributor_name so health_sweep can trigger an immediate reclaim.
        from stackowl.scheduler.handlers.task_liveness_sweep import TaskLivenessSweepHandler

        task_liveness_handler = TaskLivenessSweepHandler(db=db, backend=backend)
        healers[task_liveness_handler.contributor_name] = task_liveness_handler
        health_aggregator.register(task_liveness_handler)
        HandlerRegistry.instance().register(task_liveness_handler)
        # ADR-6 Task 3 — Kuzu may be degraded to None (gateway role / init
        # failure per DUR-5); only wire a healer when there's a live adapter to
        # recycle. Keyed by the REAL contributor_name (not a literal) so this can
        # never drift from what `_build_health_aggregator` registers below — the
        # exact class of mismatch the Task-1 embeddings healer key had.
        if memory_components.kuzu_adapter is not None:
            healers[memory_components.graph_health.contributor_name] = (
                memory_components.kuzu_adapter
            )
        for provider in provider_registry.all():
            healers[f"provider:{provider.name}"] = provider
        # ADR-6 Task 4 — Telegram adapter self-heal (thin HealableResource wrapper).
        # The adapter auto-registers itself with ChannelRegistry on start(); fetch it
        # and wire it to the health loop if telegram is configured. Gates on both
        # telegram being enabled AND the adapter being live (registered), so a
        # missing/unconfigured adapter doesn't block the sweep.
        if settings.telegram_channel.bot_token:
            from stackowl.channels.registry import ChannelRegistry

            try:
                # 1. ENTRY
                log.scheduler.debug(
                    "[scheduler] assembly: telegram healer setup — entry",
                    extra={"_fields": {"telegram_configured": True}},
                )
                channel_registry = ChannelRegistry.instance()
                telegram_adapter = channel_registry.get("telegram")
                if telegram_adapter is not None:
                    # 2. DECISION — adapter is registered and ready
                    # 3. STEP — add to healers and register contributor
                    healers[telegram_adapter.contributor_name] = telegram_adapter
                    health_aggregator.register(telegram_adapter)
                    log.scheduler.debug(
                        "[scheduler] assembly: telegram healer wired",
                        extra={
                            "_fields": {
                                "key": telegram_adapter.contributor_name,
                                "adapter": type(telegram_adapter).__name__,
                            }
                        },
                    )
                else:
                    # Adapter not yet started (will register itself later)
                    log.scheduler.debug(
                        "[scheduler] assembly: telegram adapter not yet registered — "
                        "health detection via ChannelLivenessContributor only"
                    )
            except Exception as exc:
                # 4. EXIT (error path) — log loudly but don't crash assembly
                log.scheduler.warning(
                    "[scheduler] assembly: telegram healer setup failed — "
                    "health detection via ChannelLivenessContributor only",
                    exc_info=exc,
                )
        # ADR-6 Task 5 — Discord adapter self-heal (thin HealableResource wrapper).
        # The adapter auto-registers itself with ChannelRegistry on start(); fetch it
        # and wire it to the health loop if discord is configured. Gates on both
        # discord being enabled AND the adapter being live (registered), so a
        # missing/unconfigured adapter doesn't block the sweep.
        if settings.discord_channel.bot_token:
            from stackowl.channels.registry import ChannelRegistry

            try:
                # 1. ENTRY
                log.scheduler.debug(
                    "[scheduler] assembly: discord healer setup — entry",
                    extra={"_fields": {"discord_configured": True}},
                )
                channel_registry = ChannelRegistry.instance()
                discord_adapter = channel_registry.get("discord")
                if discord_adapter is not None:
                    # 2. DECISION — adapter is registered and ready
                    # 3. STEP — add to healers and register contributor
                    healers[discord_adapter.contributor_name] = discord_adapter
                    health_aggregator.register(discord_adapter)
                    log.scheduler.debug(
                        "[scheduler] assembly: discord healer wired",
                        extra={
                            "_fields": {
                                "key": discord_adapter.contributor_name,
                                "adapter": type(discord_adapter).__name__,
                            }
                        },
                    )
                else:
                    # Adapter not yet started (will register itself later)
                    log.scheduler.debug(
                        "[scheduler] assembly: discord adapter not yet registered — "
                        "no self-heal until adapter starts"
                    )
            except Exception as exc:
                # 4. EXIT (error path) — log loudly but don't crash assembly
                log.scheduler.warning(
                    "[scheduler] assembly: discord healer setup failed — "
                    "no self-heal for discord",
                    exc_info=exc,
                )
        # ADR-6 Task 6 — Slack adapter self-heal (thin HealableResource wrapper).
        # The adapter auto-registers itself with ChannelRegistry on start(); fetch it
        # and wire it to the health loop if slack is configured. Gates on both
        # slack being enabled AND the adapter being live (registered), so a
        # missing/unconfigured adapter doesn't block the sweep.
        if settings.slack_channel.bot_token:
            from stackowl.channels.registry import ChannelRegistry

            try:
                # 1. ENTRY
                log.scheduler.debug(
                    "[scheduler] assembly: slack healer setup — entry",
                    extra={"_fields": {"slack_configured": True}},
                )
                channel_registry = ChannelRegistry.instance()
                slack_adapter = channel_registry.get("slack")
                if slack_adapter is not None:
                    # 2. DECISION — adapter is registered and ready
                    # 3. STEP — add to healers and register contributor
                    healers[slack_adapter.contributor_name] = slack_adapter
                    health_aggregator.register(slack_adapter)
                    log.scheduler.debug(
                        "[scheduler] assembly: slack healer wired",
                        extra={
                            "_fields": {
                                "key": slack_adapter.contributor_name,
                                "adapter": type(slack_adapter).__name__,
                            }
                        },
                    )
                else:
                    # Adapter not yet started (will register itself later)
                    log.scheduler.debug(
                        "[scheduler] assembly: slack adapter not yet registered — "
                        "health detection via ChannelLivenessContributor only"
                    )
            except Exception as exc:
                # 4. EXIT (error path) — log loudly but don't crash assembly
                log.scheduler.warning(
                    "[scheduler] assembly: slack healer setup failed — "
                    "no self-heal for slack",
                    exc_info=exc,
                )
        # ADR-6 Task 7 — WhatsApp adapter self-heal. Unlike Telegram/Discord/Slack
        # (thin reconnector wrappers), WhatsApp owns its Playwright browser driver
        # directly, so its ensure_available() performs a REAL browser-driver
        # restart (stop the dead WhatsAppBrowserDriver, construct+start a fresh
        # one) rather than delegating to an injected callback. The adapter
        # auto-registers itself with ChannelRegistry on start(); fetch it and wire
        # it to the health loop if whatsapp is enabled. Gates on both whatsapp
        # being enabled AND the adapter being live (registered), so a
        # missing/unconfigured adapter doesn't block the sweep. WhatsApp Web has
        # no bot token (QR-auth) — ``enabled`` is its gate, mirroring the
        # orchestrator's own startup gate for this channel.
        if settings.whatsapp_channel.enabled:
            from stackowl.channels.registry import ChannelRegistry

            try:
                # 1. ENTRY
                log.scheduler.debug(
                    "[scheduler] assembly: whatsapp healer setup — entry",
                    extra={"_fields": {"whatsapp_configured": True}},
                )
                channel_registry = ChannelRegistry.instance()
                whatsapp_adapter = channel_registry.get("whatsapp")
                if whatsapp_adapter is not None:
                    # 2. DECISION — adapter is registered and ready
                    # 3. STEP — add to healers and register contributor
                    healers[whatsapp_adapter.contributor_name] = whatsapp_adapter
                    health_aggregator.register(whatsapp_adapter)
                    log.scheduler.debug(
                        "[scheduler] assembly: whatsapp healer wired",
                        extra={
                            "_fields": {
                                "key": whatsapp_adapter.contributor_name,
                                "adapter": type(whatsapp_adapter).__name__,
                            }
                        },
                    )
                else:
                    # Adapter not yet started (will register itself later)
                    log.scheduler.debug(
                        "[scheduler] assembly: whatsapp adapter not yet registered — "
                        "health detection via ChannelLivenessContributor only"
                    )
            except Exception as exc:
                # 4. EXIT (error path) — log loudly but don't crash assembly
                log.scheduler.warning(
                    "[scheduler] assembly: whatsapp healer setup failed — "
                    "no self-heal for whatsapp",
                    exc_info=exc,
                )
        # ADR-6 Task 8 — MCP servers liveness detection. McpClient itself is a pure
        # no-op HealableResource (fully stateless per-call with bounded retry), so
        # the real value is the McpHealthContributor which aggregates probe results
        # from all configured servers. Unlike the other resources, MCP needs the
        # ServerConfig list (not just the client handle) to probe. Wire only when
        # MCP is configured and the client exists.
        if mcp_client is not None and settings.mcp_client:
            from stackowl.health.contributors import McpHealthContributor

            try:
                # 1. ENTRY
                log.scheduler.debug(
                    "[scheduler] assembly: mcp healer setup — entry",
                    extra={"_fields": {"mcp_configured": True}},
                )
                # 2. DECISION — MCP client exists and servers are configured
                mcp_configs = list(settings.mcp_client.servers)
                if mcp_configs:
                    # 3. STEP — add to healers and register contributor
                    # Extract the probe from the mcp_client (it holds McpLivenessProbe internally)
                    mcp_probe = getattr(mcp_client, "_probe", None)
                    if mcp_probe is None:
                        raise RuntimeError("McpClient has no _probe attribute")
                    mcp_health = McpHealthContributor(
                        probe=mcp_probe,
                        configs=mcp_configs,
                    )
                    healers[mcp_health.contributor_name] = mcp_client  # type: ignore[index]
                    health_aggregator.register(mcp_health)
                    log.scheduler.debug(
                        "[scheduler] assembly: mcp healer wired",
                        extra={
                            "_fields": {
                                "key": mcp_health.contributor_name,
                                "servers": len(mcp_configs),
                            }
                        },
                    )
                else:
                    log.scheduler.debug(
                        "[scheduler] assembly: mcp configured but no servers — "
                        "skipping health detection"
                    )
            except Exception as exc:
                # 4. EXIT (error path) — log loudly but don't crash assembly
                log.scheduler.warning(
                    "[scheduler] assembly: mcp healer setup failed — "
                    "no health detection for mcp",
                    exc_info=exc,
                )
        # Browser needs both DETECT (live BrowserContributor) and HEAL (the runtime).
        # Adding a contributor changes the detect set, so gate it on the flag too —
        # OFF leaves the aggregator's contributor set unchanged (byte-identical).
        if settings.health_loop and browser_runtime is not None:
            from stackowl.health.contributors import BrowserContributor

            health_aggregator.register(
                BrowserContributor(browser_runtime, sessions=None)
            )
            healers["browser"] = browser_runtime

        health_sweep_handler = HealthSweepHandler(
            health_aggregator, alert=health_alert, healers=healers
        )
        HandlerRegistry.instance().register(health_sweep_handler)

        # ADR-6 Task 6 — INCIDENT ESCALATION. The sweep above is the detect+recycle
        # half of the self-heal loop; this is the escalate half. When recycle/retry/
        # substitution has ALREADY run and a subsystem/capability is STILL broken on
        # a later tick, it drives a fixed-stage (NOT open-debate) root-cause analysis.
        # It REUSES the sweep's alert-state map (no second health tracker) for
        # still-unhealthy subsystems and Task 5's failure clustering for the durable
        # footprint of a recurring in-turn self-heal. Stops at a verified/fallback
        # RcaVerdict (Task 7 consumes it). Flag-gated on the same health_loop switch.
        from functools import partial

        from stackowl.learning.failure_outcome_miner import FailureOutcomeMiner
        from stackowl.memory.outcome_store import TaskOutcomeStore
        from stackowl.parliament.staged_rca import StagedRcaSession
        from stackowl.paths import StackowlHome
        from stackowl.scheduler.handlers.incident_escalation import (
            IncidentEscalationHandler,
        )
        from stackowl.scheduler.handlers.rca_verdict_router import route_rca_verdict

        # Task 7 — the SAME FailureOutcomeMiner shape SkillSynthesizerHandler uses
        # (skill_store/skills_root/consent_gate), pointed at the incident-mining
        # side of task_outcomes instead of the success-clustering side. Wired as
        # IncidentEscalationHandler's consumer of a CONCLUDED RCA verdict — it
        # authors a learned SKILL.md via the SAME gated_skill_write chokepoint
        # (Task 4) whenever a cluster both meets the evidence threshold AND has a
        # matching verified verdict; otherwise it is a no-op, never partial.
        incident_miner = FailureOutcomeMiner(
            outcome_store=TaskOutcomeStore(db),
            skill_store=skills_components.store,
            skills_root=StackowlHome.skills_dir(),
            consent_gate=consent_gate,
        )

        incident_escalation_handler = IncidentEscalationHandler(
            health_sweep=health_sweep_handler,
            outcome_store=TaskOutcomeStore(db),
            rca_session=StagedRcaSession(backend),
            # Task 7 consumption hooks — see rca_verdict_router.py docstring for
            # why delegate_task's ladder is NOT wired here (a live-turn-only
            # mechanism; wiring it from a scheduler tick would fake a user turn).
            verdict_router=partial(route_rca_verdict, tool_registry=tool_registry),
            miner=incident_miner,
            # SAME alert sink health_sweep already uses — an incident verdict
            # rides the existing operator-alert channel, not a new one.
            alert=health_alert,
        )
        HandlerRegistry.instance().register(incident_escalation_handler)

        # Report the ACTUAL registered set (self-maintaining — a hardcoded count
        # had drifted, omitting reflection_writer/skill_synthesizer/
        # tool_outcome_miner). Honest wiring logs are the whole point of this arc.
        _registered = sorted(h.handler_name for h in HandlerRegistry.instance().list())
        log.scheduler.info(
            "[scheduler] assembly: handlers registered",
            extra={"_fields": {"count": len(_registered), "handlers": _registered}},
        )

        # 4) Auto-schedule three per operator vote (morning_brief, evolution,
        # knowledge_prune). The remaining three are register-only and get
        # enqueued on user demand (e.g., goal_execution per /goal-add command).
        brief_channels = list(settings.brief.channels)
        await _seed_daily_schedule(
            db, handler_name="morning_brief",
            schedule="daily@08:00", next_hour=8,
            target_channels=brief_channels,
            target_addresses=_resolve_owner_addresses(settings, brief_channels),
        )
        # WS-C — check_in is a built+registered+honest-delivering handler that had
        # NO producer: nothing ever seeded its jobs row, so the scheduler never
        # dispatched it (a promised periodic outreach that never fired). Seed it
        # exactly like morning_brief, gated on settings.check_in.enabled.
        if settings.check_in.enabled:
            check_in_channels = list(settings.check_in.channels)
            check_in_addresses = _resolve_owner_addresses(settings, check_in_channels)
            if check_in_addresses:
                # check_in.schedule is USER-CONFIGURABLE (unlike the hardcoded
                # daily@HH:MM constants of the other seeds), so derive the first-run
                # hour FROM it — never hardcode a next_hour that could diverge from
                # the stored schedule string. Falls back to 18 for a non-daily@ value.
                check_in_hour = _daily_schedule_hour(settings.check_in.schedule, 18)
                await _seed_daily_schedule(
                    db, handler_name="check_in",
                    schedule=settings.check_in.schedule, next_hour=check_in_hour,
                    target_channels=check_in_channels,
                    target_addresses=check_in_addresses,
                )
            else:
                # HONESTY: never seed a target-less, permanently-undeliverable row.
                # Unlike morning_brief (which seeds a row even with empty addresses),
                # check_in only schedules a DELIVERABLE occurrence — a no-recipient
                # row would be a silent no-op every poll. Warn loudly instead.
                log.scheduler.warning(
                    "[scheduler] check_in enabled but has no resolvable recipient — "
                    "NOT scheduled. Cause: no single resolvable owner for these "
                    "channels (e.g. a non-telegram channel like 'cli' has no durable "
                    "proactive address, or the telegram allowlist is empty/ambiguous)",
                    extra={"_fields": {"channels": check_in_channels}},
                )
        else:
            log.scheduler.debug(
                "[scheduler] check_in disabled — skipping seed",
            )
        # PB-CANARY — synthetic telegram send-path round-trip heartbeat: every
        # 20m, send a small marker through the real Telegram Bot API; a
        # confirmed 'delivered' stamps the send-path liveness row the second
        # ChannelLivenessContributor (kind="send") reads, alerting on absence via
        # the existing HealthSweepHandler/AlertSink path. Ships ON by default
        # (no settings flag) — same honesty rule as check_in: never seed a
        # permanently-undeliverable row when telegram has no single resolvable
        # owner.
        canary_channels = ["telegram"]
        canary_addresses = _resolve_owner_addresses(settings, canary_channels)
        if canary_addresses:
            await _seed_minutes_schedule(
                db, handler_name="telegram_canary", schedule="every 20m",
                interval_minutes=20,
                target_channels=canary_channels, target_addresses=canary_addresses,
            )
        else:
            log.scheduler.warning(
                "[scheduler] telegram_canary has no resolvable recipient — NOT "
                "scheduled (no single resolvable telegram owner)",
            )
        # LANDMINE: _seed_daily_schedule is idempotent by handler_name (early-returns
        # if a row exists). So flipping enabled OFF→ON (or fixing the recipient) AFTER
        # a row already exists will NOT re-stamp the durable target. Since we only ever
        # seed deliverable rows this is safe today; documented, not fixed (out of scope).
        # EvolutionCoordinator registers itself under handler_name="evolution_batch".
        await _seed_daily_schedule(
            db, handler_name="evolution_batch",
            schedule="daily@02:00", next_hour=2,
        )
        await _seed_daily_schedule(
            db, handler_name="knowledge_prune",
            schedule="daily@04:00", next_hour=4,
        )
        # Objective driver advances standing objectives one sub-goal per tick.
        # Frequent (1m) so a multi-step objective makes visible progress; each
        # tick is cheap when there are no active objectives.
        await _seed_minutes_schedule(
            db, handler_name="objective_driver", schedule="every 1m",
            interval_minutes=1,
        )
        # Retry sweep — retries floored turns every minute, capped at 3
        # attempts per retry_queue row (RetrySweepHandler / RetryActuator).
        await _seed_minutes_schedule(
            db, handler_name="retry_sweep", schedule="every 1m",
            interval_minutes=1,
        )
        # F-77 — notification_digest flushes the notification_queue (batched /
        # quiet-hours notifications). The handler is built+registered (in
        # NotificationAssembly), but without a recurring jobs row the scheduler
        # never dispatches it and batched bodies age in the queue forever. Seed
        # it HERE, co-located with the scheduler that actually polls it, so the
        # flush fires regardless of how NotificationAssembly's path evolves.
        # Unlike check_in (a per-recipient send), the digest is a flush job:
        # each queued row already carries its OWN channel, so there is no single
        # durable target to resolve — it is seeded unconditionally like the
        # other every-Nm maintenance sweeps. Idempotent by handler_name (a no-op
        # if NotificationAssembly already seeded the row this boot). 5m matches
        # the digest's batching cadence.
        await _seed_minutes_schedule(
            db, handler_name="notification_digest", schedule="every 5m",
            interval_minutes=5,
        )
        # F-87 — health sweep every 5m: collect in-process health, alert on
        # down/degraded. Cheap when everything is healthy (a quiet debug exit).
        await _seed_minutes_schedule(
            db, handler_name="health_sweep", schedule="every 5m",
            interval_minutes=5,
        )
        # ADR-6 Task 6 — incident escalation every 10m (heavier than the sweep: it
        # runs a 3-stage RCA on a NEW incident). Its dedupe ensures a subsystem that
        # stays broken across ticks yields ONE RCA session, not one per tick.
        await _seed_minutes_schedule(
            db, handler_name="incident_escalation", schedule="every 10m",
            interval_minutes=10,
        )
        # Task 9 — task liveness watchdog. Interval is a FRACTION of
        # DEFAULT_STALE_AFTER_S (10m), mirroring the codebase's existing
        # TTL-sweep convention (clarify_sweep/session_sweep run at TTL/3) so a
        # stuck task is never left ~2x the staleness threshold before reclaim.
        await _seed_minutes_schedule(
            db, handler_name="task_liveness_sweep", schedule="every 3m",
            interval_minutes=3,
        )
        # FR-4 — reflection_writer now scores (composed CriticScorerHandler)
        # THEN reflects in one run; the separate critic_scorer job/seed row is
        # gone (was every 10m — see handler docstring for the merge rationale).
        await _seed_minutes_schedule(
            db, handler_name="reflection_writer", schedule="every 15m",
            interval_minutes=15,
        )
        # Clarify sweep reaps abandoned turn-yield clarify entries (blocking ones
        # self-reap via their own park timeout). The INTERVAL is intentionally a
        # FRACTION of CLARIFY_TTL_SECONDS (30m TTL) so an aged entry never lives
        # ~2×TTL before being swept — interval << TTL is deliberate.
        await _seed_minutes_schedule(
            db, handler_name="clarify_sweep", schedule="every 10m",
            interval_minutes=10,
        )
        # E8-S3 — session sweep reaps named owl sessions idle past
        # SESSION_IDLE_TTL_SECONDS (30m), draining each reaped session's A2A
        # mailbox. Interval (10m) is a fraction of the TTL so an abandoned session
        # never lives ~2×TTL before reaping. The handler is registered in the
        # gateway assembly (it needs the SessionRegistry singleton); this only
        # seeds the recurring jobs row so the scheduler actually dispatches it.
        await _seed_minutes_schedule(
            db, handler_name="session_sweep", schedule="every 10m",
            interval_minutes=10,
        )
        # E9-S0 — process sweep drives the ProcessRegistry maintenance every
        # SWEEP_INTERVAL_SECONDS (10m): auto-kills any process past its MANDATORY
        # max lifetime, prunes dead handles past the prune TTL, and enforces the
        # aggregate capture-buffer ceiling. The handler is registered in the gateway
        # assembly (it needs the ProcessRegistry singleton); this only seeds the
        # recurring jobs row so the scheduler actually dispatches it.
        await _seed_minutes_schedule(
            db, handler_name="process_sweep", schedule="every 10m",
            interval_minutes=10,
        )
        # F050 — turn sweep is the BACKSTOP reaper for a turn left RUNNING after a
        # missed completion hook (task done() but status never reached DONE), which
        # wedges TurnRegistry._running[session_id] forever and jams all later
        # same-session routing. Every 10m (well under the host-scaled TTL) it
        # deregisters such wedged turns and surfaces any reaped-but-stranded session
        # to the drain seam. The handler is registered in the gateway assembly (it
        # needs the TurnRegistry singleton + the drain seam); this seeds the row.
        await _seed_minutes_schedule(
            db, handler_name="turn_sweep", schedule="every 10m",
            interval_minutes=10,
        )
        # E11-S6 — sandbox sweep reaps LEAKED sandbox artifacts a crash/kill left
        # behind: scratch dirs under ~/.stackowl/sandbox/ + stackowl-sbx-* docker
        # containers + bwrap cgroup scopes, each older than SANDBOX_ARTIFACT_TTL_S
        # (1h, ~120× the 30s max wall time so a LIVE run is never reaped). The
        # handler is registered in the gateway assembly (stateless reaper); this only
        # seeds the recurring jobs row so the scheduler actually dispatches it.
        await _seed_minutes_schedule(
            db, handler_name="sandbox_sweep", schedule="every 10m",
            interval_minutes=10,
        )
        # Downloads janitor — prune the single workspace downloads folder every
        # 12h (720m), deleting files older than 2 days (the handler's default
        # retention). Scoped to that folder ONLY; never touches durable stores.
        await _seed_minutes_schedule(
            db, handler_name="downloads_janitor", schedule="every 12h",
            interval_minutes=720,
        )
        # Skill synthesizer runs once per day at 03:30 (between knowledge_prune
        # at 04:00 and evolution at 02:00) — needs ≥several days of outcomes
        # to find qualifying clusters, so daily is the right cadence.
        await _seed_daily_schedule(
            db, handler_name="skill_synthesizer",
            schedule="daily@03:30", next_hour=3,
        )
        # Tool outcome miner runs daily at 05:00 — after all the other
        # scheduled work has populated quality scores and outcome data.
        await _seed_daily_schedule(
            db, handler_name="tool_outcome_miner",
            schedule="daily@05:00", next_hour=5,
        )

        log.scheduler.info("[scheduler] assembly.build: exit — all wired")
        return SchedulerComponents(
            scheduler=scheduler,
            supervisor=supervisor,
            morning_brief_handler=morning_brief_handler,
            check_in_handler=check_in_handler,
            knowledge_prune_handler=knowledge_prune_handler,
            tool_pruning_handler=tool_pruning_handler,
            goal_execution_handler=goal_execution_handler,
            objective_driver_handler=objective_driver_handler,
            reflection_writer_handler=reflection_writer_handler,
            skill_synthesizer_handler=skill_synthesizer_handler,
            tool_outcome_miner_handler=tool_outcome_miner_handler,
            health_sweep_handler=health_sweep_handler,
            incident_escalation_handler=incident_escalation_handler,
        )


def _build_health_aggregator(
    settings: Settings,
    liveness_store: ChannelLivenessStore | None = None,
    embedding_registry: EmbeddingRegistry | None = None,
    lancedb_adapter: LanceDBAdapter | None = None,
    graph_contributor: GraphContributor | None = None,
) -> HealthAggregator:
    """Build an in-process HealthAggregator from the LOCAL contributors (F-87).

    Mirrors the ``stackowl health`` CLI's contributor set MINUS the ones that need
    live-runtime refs (Browser/Resilience) — those would only report "not
    constructed" from a background sweep. Db / filesystem / graph / enabled
    providers all probe from durable config alone, so the periodic sweep reports
    TRUTHFUL status without threading live serve-process resources.

    ``liveness_store`` is the ONE shared :class:`ChannelLivenessStore` instance
    built by the caller (gated on ``telegram_channel.bot_token`` — no telegram
    configured means no signal to produce or consume). ``None`` skips both
    registrations below, exactly as before this PB-CANARY generalization.

    ``embedding_registry`` (Task 1, ADR-6 self-heal) is the live, in-process
    :class:`EmbeddingRegistry` — unlike Browser/Resilience it needs no
    live-runtime handle beyond what ``MemoryAssembly.build`` already
    constructed, and it already implements the contributor shape
    (``contributor_name`` + ``health_check``) directly, so it's registered
    unconditionally here exactly like ``DbContributor``. ``None`` (a caller
    that hasn't threaded it through) skips registration, same pattern as
    ``liveness_store``.

    ``lancedb_adapter`` (Task 2, ADR-6 self-heal) is the live, in-process
    :class:`LanceDBAdapter` — same "no extra live-runtime handle needed"
    situation as ``embedding_registry``, but unlike it LanceDBAdapter's
    existing ``health()`` returns a ``HealthReport`` (a different shape), so
    it's wrapped in :class:`LanceDBHealthContributor` rather than registered
    directly. ``None`` skips registration, same pattern as the others.

    ``graph_contributor`` (Task 3, ADR-6 self-heal) is
    ``MemoryComponents.graph_health`` — a :class:`GraphContributor` already
    built by ``MemoryAssembly.build`` with the live Kuzu adapter wired in (or a
    degrade-at-boot cached snapshot when Kuzu is None). Registering that SAME
    object here — rather than building a fresh one — means the aggregator's
    live-probing verdict and the ``healers`` dict entry above are guaranteed to
    agree, since both read `.contributor_name` off the identical instance.
    ``None`` falls back to the old import-only ``GraphContributor.probe()`` (no
    caller passes ``None`` today, but this keeps the function's own default
    behaviour self-contained).
    """
    from stackowl.db.pool import default_db_path
    from stackowl.health.aggregator import HealthAggregator
    from stackowl.health.contributors import (
        ChannelLivenessContributor,
        DbContributor,
        FilesystemContributor,
        GraphContributor,
        LanceDBHealthContributor,
        ProviderContributor,
    )
    from stackowl.infra.clock import WallClock
    from stackowl.startup.fs_probe import _data_dir, _log_dir

    agg = HealthAggregator()
    agg.register(DbContributor(default_db_path()))
    agg.register(FilesystemContributor(_data_dir(), _log_dir()))
    agg.register(graph_contributor if graph_contributor is not None else GraphContributor.probe())
    if embedding_registry is not None:
        agg.register(embedding_registry)
    if lancedb_adapter is not None:
        agg.register(LanceDBHealthContributor(lancedb_adapter))
    for provider in settings.providers:
        if provider.enabled:
            agg.register(ProviderContributor(provider))
    # RC0 — telegram receive-loop liveness. Only registered when telegram is
    # configured, else it would falsely report "down" for a channel the operator
    # never enabled. Reads the cross-process channel_liveness row.
    if liveness_store is not None:
        agg.register(ChannelLivenessContributor(liveness_store, "telegram", WallClock()))
        # PB-CANARY — send-path sibling signal: a confirmed real send (not just
        # the receive loop) stamps this row. stale_after_s = 2x the 20-min canary
        # interval so one missed tick isn't a false alarm, but a genuinely dead
        # canary is caught within ~40 min.
        agg.register(
            ChannelLivenessContributor(
                liveness_store, "telegram_canary", WallClock(),
                kind="send", stale_after_s=2400.0,
            )
        )
    return agg


def _build_health_alert_sink(
    proactive_deliverer: ProactiveDeliverer | None, settings: Settings
) -> Callable[[str], Awaitable[None]] | None:
    """An operator-alert sink for the health sweep, or None if undeliverable.

    Builds a closure that sends a CRITICAL operator notification through the same
    :class:`ProactiveDeliverer` seam the briefs use. Returns None when no deliverer
    is wired (the sweep then alerts via its LOUD log only — never a fake "sent").
    """
    if proactive_deliverer is None:
        return None
    brief_channels = list(settings.brief.channels)
    channel = brief_channels[0] if brief_channels else None
    # Health degraded/recovered flaps are operator noise, not durable content —
    # they should not linger in the chat forever. Reuse the SAME ephemeral
    # (send-then-self-delete) path the health-canary probe already uses
    # (Notification.ephemeral, deliverer._transport) instead of a normal
    # permanent send. Requires a resolved chat_id (telegram only); falls back
    # to a normal visible send on any other channel or if resolution fails.
    target_chat_id: str | int | None = None
    if channel:
        target_chat_id = _resolve_owner_addresses(settings, [channel]).get(channel)

    async def _alert(message: str) -> None:
        from stackowl.notifications.router import Notification

        note = Notification(
            message=message,
            urgency="critical",
            category="operator_health",
            channel_name=channel,
            target=target_chat_id,
            ephemeral=True,
        )
        await proactive_deliverer.deliver(note)

    return _alert


def _resolve_owner_addresses(
    settings: Settings, channels: list[str]
) -> dict[str, str | int]:
    """Thin wrapper — delegates to the shared ``notifications.recipient`` resolver.

    The owner→native-target logic moved into :mod:`stackowl.notifications.recipient`
    (next to :class:`DeliverySpec`) so every producer path shares ONE resolver. This
    wrapper preserves the in-package call sites; behavior is identical.
    """
    from stackowl.notifications.recipient import resolve_owner_addresses

    return resolve_owner_addresses(settings, channels)


def _daily_schedule_hour(schedule: str, default: int) -> int:
    """Parse the HH from a ``daily@HH:MM`` schedule; ``default`` if not that shape.

    Used to keep a seeded job's first-run hour in lockstep with a
    user-configurable ``daily@HH:MM`` schedule string (so they can never diverge).
    A non-daily@ schedule (e.g. an interval) returns ``default``.
    """
    if schedule.startswith("daily@"):
        try:
            hour = int(schedule[len("daily@"):].split(":")[0])
        except (ValueError, IndexError):
            return default
        # Guard the range: _next_local_hour_iso → datetime.replace(hour=) raises
        # for hour>23, which would abort assembly. (CheckInSettings already
        # validates this; belt-and-suspenders for any other caller.)
        if 0 <= hour <= 23:
            return hour
        return default
    return default


def _next_local_hour_iso(hour: int) -> str:
    """Return the next local-time HH:00 as an ISO8601 UTC string."""
    now = datetime.now()
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC).isoformat()


async def _seed_daily_schedule(
    db: DbPool,
    *,
    handler_name: str,
    schedule: str,
    next_hour: int,
    target_channels: list[str] | None = None,
    target_addresses: dict[str, str | int] | None = None,
) -> None:
    """Idempotent: insert one `jobs` row for ``handler_name`` if none exists.

    ``target_channels`` / ``target_addresses`` stamp the DURABLE recipient on the
    seeded row (C1/F101) so a cron-born poll can address its send from durable
    state. When provided the row is inserted via the shared ``insert_job`` (the
    full SQL that persists the target columns); otherwise the legacy short insert
    is kept byte-identical for handlers with no proactive recipient.
    """
    existing = await db.fetch_all(_SELECT_EXISTING_SQL, (handler_name,))
    if existing:
        log.scheduler.debug(
            "[scheduler] schedule seed: already present — noop",
            extra={"_fields": {"handler": handler_name}},
        )
        return
    job_id = f"{handler_name}-{uuid.uuid4().hex[:8]}"
    if target_channels:
        # Stamp the durable recipient — reuse the helper insert that persists the
        # target columns rather than a parallel SQL string.
        from stackowl.scheduler.job import Job
        from stackowl.scheduler.scheduler_helpers import insert_job

        job = Job(
            job_id=job_id,
            handler_name=handler_name,
            schedule=schedule,
            idempotency_key=f"{handler_name}:daily",
            last_run_at=None,
            next_run_at=_next_local_hour_iso(next_hour),
            status="pending",
            target_channels=list(target_channels),
            target_addresses=dict(target_addresses or {}),
        )
        await insert_job(db, job)
        log.scheduler.info(
            "[scheduler] schedule seeded (durable target)",
            extra={
                "_fields": {
                    "handler": handler_name,
                    "schedule": schedule,
                    "job_id": job_id,
                    "target_channels": list(target_channels),
                    "addressed_channels": sorted(target_addresses or {}),
                }
            },
        )
        return
    now_iso = datetime.now(UTC).isoformat()
    await db.execute(
        _INSERT_JOB_SQL,
        (
            job_id,
            handler_name,
            schedule,
            f"{handler_name}:daily",
            None,
            _next_local_hour_iso(next_hour),
            "pending",
            0,
            now_iso,
        ),
    )
    log.scheduler.info(
        "[scheduler] schedule seeded",
        extra={"_fields": {"handler": handler_name, "schedule": schedule, "job_id": job_id}},
    )


async def seed_browser_maintenance_schedules(db: DbPool) -> None:
    """Idempotently seed the LOCAL browser-maintenance jobs (WS-G).

    Three fully-built handlers (``profile_backup``, ``browser_cache_eviction``,
    ``browser_recycle``) are registered ONLY when a browser runtime is available
    (see ``startup/orchestrator.py``), but nothing ever produced their ``jobs``
    rows — so the poll loop never dispatched them and the WS-E wiring audit
    flagged all three as DANGLING. These are LOCAL maintenance jobs: NO delivery
    target (no ``target_channels`` / ``target_addresses``), fixed daily cadence,
    empty params (each handler runs correctly with its built-in defaults).

    MUST be called from the same browser-available block that REGISTERS these
    handlers — never seed a row for a handler that isn't registered (the scheduler
    would error every poll on an unknown handler). Idempotent by ``handler_name``
    (``_seed_daily_schedule`` early-returns if a row exists), so boot re-runs are
    safe.

    Cadence (all daily, overnight, STAGGERED to avoid runtime contention):

    * ``profile_backup`` @01:00 — tars persistent profile dirs (login state) and
      prunes to the handler's retention default. Daily is conservative: a
      logged-in profile that breaks/gets deleted is recoverable from a ≤24h-old
      archive. Runs first (before recycle/eviction touch anything).
    * ``browser_recycle`` @03:00 — backstop forced runtime recycle + idle-session
      evict. The runtime already self-recycles on nav-count/idle and the live
      session sweep runs every 10m, so this is purely a low-traffic (overnight)
      belt-and-suspenders tick — daily is the right, non-aggressive cadence.
    * ``browser_cache_eviction`` @04:30 — prunes cache (>7d) + screenshots (>30d)
      by the handler's age defaults. Daily keeps disk bounded; runs LAST so the
      day's recycle/backup artifacts settle before the prune pass.

    NOT seeded here (deliberate): ``screenshot_archive`` and
    ``credential_rotation`` REQUIRE per-job ``params`` (a URL list; a
    profile+check_url) and would return ``success=False`` on EVERY poll if seeded
    blank. They are genuinely ``on_demand`` — enqueued per user-configured target,
    exactly like ``goal_execution`` — and declare ``trigger_kind='on_demand'`` so
    the WS-E audit does not flag them as dangling. Seeding a perpetually-failing
    blank row would be the very anti-pattern this arc exists to kill.
    """
    log.scheduler.info("[scheduler] seed_browser_maintenance_schedules: entry")
    # profile_backup @01:00 — recover login state from a ≤24h-old archive.
    await _seed_daily_schedule(
        db, handler_name="profile_backup",
        schedule="daily@01:00", next_hour=1,
    )
    # browser_recycle @03:00 — low-traffic backstop for the FF RSS leak; the
    # runtime + 10m session sweep already self-recycle, so daily is sufficient.
    await _seed_daily_schedule(
        db, handler_name="browser_recycle",
        schedule="daily@03:00", next_hour=3,
    )
    # browser_cache_eviction @04:30 — bound disk; runs after backup/recycle settle.
    await _seed_daily_schedule(
        db, handler_name="browser_cache_eviction",
        schedule="daily@04:30", next_hour=4,
    )
    log.scheduler.info("[scheduler] seed_browser_maintenance_schedules: exit — 3 seeded")


async def _seed_minutes_schedule(
    db: DbPool, *, handler_name: str, schedule: str, interval_minutes: int,
    target_channels: list[str] | None = None,
    target_addresses: dict[str, str | int] | None = None,
) -> None:
    """Idempotent: insert one ``jobs`` row for a frequent (minute-scale) handler.

    ``target_channels``/``target_addresses`` optionally stamp a durable
    recipient (C1/F101) — mirrors ``_seed_daily_schedule``'s target-stamping
    branch, for a minute-scale handler whose ``execute()`` must resolve a real
    send target (e.g. PB-CANARY's send-path proof). Omitted (the default for
    every existing every-Nm caller) preserves the exact prior no-target insert.
    """
    existing = await db.fetch_all(_SELECT_EXISTING_SQL, (handler_name,))
    if existing:
        log.scheduler.debug(
            "[scheduler] schedule seed (minutes): already present — noop",
            extra={"_fields": {"handler": handler_name}},
        )
        return
    job_id = f"{handler_name}-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    next_run = (now + timedelta(minutes=interval_minutes)).isoformat()
    if target_channels:
        from stackowl.scheduler.job import Job
        from stackowl.scheduler.scheduler_helpers import insert_job

        job = Job(
            job_id=job_id,
            handler_name=handler_name,
            schedule=schedule,
            idempotency_key=f"{handler_name}:every-{interval_minutes}m",
            last_run_at=None,
            next_run_at=next_run,
            status="pending",
            target_channels=list(target_channels),
            target_addresses=dict(target_addresses or {}),
        )
        await insert_job(db, job)
        log.scheduler.info(
            "[scheduler] schedule seeded (minutes, durable target)",
            extra={"_fields": {
                "handler": handler_name, "schedule": schedule,
                "interval_minutes": interval_minutes, "job_id": job_id,
                "target_channels": list(target_channels),
                "addressed_channels": sorted(target_addresses or {}),
            }},
        )
        return
    await db.execute(
        _INSERT_JOB_SQL,
        (
            job_id, handler_name, schedule,
            f"{handler_name}:every-{interval_minutes}m",
            None, next_run, "pending", 0, now.isoformat(),
        ),
    )
    log.scheduler.info(
        "[scheduler] schedule seeded (minutes)",
        extra={"_fields": {
            "handler": handler_name, "schedule": schedule,
            "interval_minutes": interval_minutes, "job_id": job_id,
        }},
    )
