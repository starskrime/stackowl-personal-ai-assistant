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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.events.bus import EventBus
    from stackowl.memory.assembly import MemoryComponents
    from stackowl.memory.critic_scorer_handler import CriticScorerHandler
    from stackowl.memory.reflection_writer_handler import ReflectionWriterHandler
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.backends.base import OrchestratorBackend
    from stackowl.providers.registry import ProviderRegistry
    from stackowl.scheduler.handlers.check_in import CheckInHandler
    from stackowl.scheduler.handlers.goal_execution import GoalExecutionHandler
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


_INSERT_JOB_SQL = """
INSERT INTO jobs
    (job_id, handler_name, schedule, idempotency_key, last_run_at,
     next_run_at, status, retry_count, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_SELECT_EXISTING_SQL = "SELECT job_id FROM jobs WHERE handler_name = ?"


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
    critic_scorer_handler: CriticScorerHandler
    reflection_writer_handler: ReflectionWriterHandler
    skill_synthesizer_handler: SkillSynthesizerHandler
    tool_outcome_miner_handler: ToolOutcomeMinerHandler


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
    ) -> SchedulerComponents:
        log.scheduler.info("[scheduler] assembly.build: entry")

        # Deferred imports — keep this module cheap when scheduler isn't used.
        from stackowl.scheduler.base import HandlerRegistry
        from stackowl.scheduler.handlers.check_in import CheckInHandler
        from stackowl.scheduler.handlers.evolution import register_evolution_handler
        from stackowl.scheduler.handlers.goal_execution import GoalExecutionHandler
        from stackowl.scheduler.handlers.knowledge_prune import KnowledgePruneHandler
        from stackowl.scheduler.handlers.morning_brief import MorningBriefHandler
        from stackowl.scheduler.handlers.tool_pruning import ToolPruningHandler
        from stackowl.scheduler.scheduler import JobScheduler
        from stackowl.supervisor.supervisor import Supervisor

        # 1) JobScheduler — the polling loop that dispatches due jobs.
        scheduler = JobScheduler(db=db)
        log.scheduler.debug("[scheduler] assembly: JobScheduler constructed")

        # 2) Supervisor — owns the scheduler's runtime task.
        supervisor = Supervisor()
        supervisor.register(scheduler)
        log.scheduler.debug("[scheduler] assembly: Supervisor wraps JobScheduler")

        # 3) Register the 6 orphaned handlers. Each uses HandlerRegistry directly
        # OR an existing factory (evolution uses register_evolution_handler).

        morning_brief_handler = MorningBriefHandler(
            memory_bridge=memory_components.bridge,
            scheduler=scheduler,
            db=db,
            event_bus=event_bus,
            settings=settings,
        )
        HandlerRegistry.instance().register(morning_brief_handler)

        check_in_handler = CheckInHandler()
        HandlerRegistry.instance().register(check_in_handler)

        knowledge_prune_handler = KnowledgePruneHandler(pruner=memory_components.pruner)
        HandlerRegistry.instance().register(knowledge_prune_handler)

        tool_pruning_handler = ToolPruningHandler()
        HandlerRegistry.instance().register(tool_pruning_handler)

        goal_execution_handler = GoalExecutionHandler(backend=backend, db=db)
        HandlerRegistry.instance().register(goal_execution_handler)

        # Evolution uses its own register factory (which owns the import + DI).
        register_evolution_handler(
            db=db,
            provider_registry=provider_registry,
            owl_registry=owl_registry,
        )

        # Learning Commit 1 — CriticScorerHandler scores pending task_outcomes
        # async (every 10 min). Reuses the existing JobHandler/scheduler pattern.
        from stackowl.memory.critic_scorer_handler import CriticScorerHandler

        critic_scorer_handler = CriticScorerHandler(
            db=db, provider_registry=provider_registry,
        )
        HandlerRegistry.instance().register(critic_scorer_handler)

        # Learning Commit 2 — ReflectionWriterHandler turns failed / low-quality
        # outcomes into Reflexion-style reflections (every 15 min). Uses the
        # MemoryAssembly's embedding registry so reflections can be retrieved
        # semantically by classify.py.
        from stackowl.memory.reflection_writer_handler import ReflectionWriterHandler

        reflection_writer_handler = ReflectionWriterHandler(
            db=db,
            provider_registry=provider_registry,
            embedding_registry=memory_components.embedding_registry,
            lessons_index=memory_components.lessons_index,
        )
        HandlerRegistry.instance().register(reflection_writer_handler)

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

        log.scheduler.info(
            "[scheduler] assembly: 7 orphaned handlers registered",
            extra={"_fields": {
                "handlers": [
                    "morning_brief", "check_in", "knowledge_prune",
                    "tool_pruning", "goal_execution", "evolution",
                    "critic_scorer",
                ],
            }},
        )

        # 4) Auto-schedule three per operator vote (morning_brief, evolution,
        # knowledge_prune). The remaining three are register-only and get
        # enqueued on user demand (e.g., goal_execution per /goal-add command).
        await _seed_daily_schedule(
            db, handler_name="morning_brief",
            schedule="daily@08:00", next_hour=8,
        )
        # EvolutionCoordinator registers itself under handler_name="evolution_batch".
        await _seed_daily_schedule(
            db, handler_name="evolution_batch",
            schedule="daily@02:00", next_hour=2,
        )
        await _seed_daily_schedule(
            db, handler_name="knowledge_prune",
            schedule="daily@04:00", next_hour=4,
        )
        # Critic scorer runs frequently — outcomes pile up fast.
        await _seed_minutes_schedule(
            db, handler_name="critic_scorer", schedule="every 10m",
            interval_minutes=10,
        )
        # Reflection writer runs slightly less often (depends on critic having
        # filled in quality_score first; 5min stagger keeps them in step).
        await _seed_minutes_schedule(
            db, handler_name="reflection_writer", schedule="every 15m",
            interval_minutes=15,
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
            critic_scorer_handler=critic_scorer_handler,
            reflection_writer_handler=reflection_writer_handler,
            skill_synthesizer_handler=skill_synthesizer_handler,
            tool_outcome_miner_handler=tool_outcome_miner_handler,
        )


def _next_local_hour_iso(hour: int) -> str:
    """Return the next local-time HH:00 as an ISO8601 UTC string."""
    now = datetime.now()
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC).isoformat()


async def _seed_daily_schedule(
    db: DbPool, *, handler_name: str, schedule: str, next_hour: int,
) -> None:
    """Idempotent: insert one `jobs` row for ``handler_name`` if none exists."""
    existing = await db.fetch_all(_SELECT_EXISTING_SQL, (handler_name,))
    if existing:
        log.scheduler.debug(
            "[scheduler] schedule seed: already present — noop",
            extra={"_fields": {"handler": handler_name}},
        )
        return
    job_id = f"{handler_name}-{uuid.uuid4().hex[:8]}"
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


async def _seed_minutes_schedule(
    db: DbPool, *, handler_name: str, schedule: str, interval_minutes: int,
) -> None:
    """Idempotent: insert one ``jobs`` row for a frequent (minute-scale) handler."""
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
