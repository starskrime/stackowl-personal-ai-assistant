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
    from stackowl.notifications.deliverer import ProactiveDeliverer
    from stackowl.owls.concurrency import ConcurrencyGovernor
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
        proactive_deliverer: ProactiveDeliverer | None = None,
        delegation_governor: ConcurrencyGovernor | None = None,
        turn_registry: object | None = None,
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
                proactive_deliverer, delivery_ledger
            )

        goal_execution_handler = GoalExecutionHandler(
            backend=backend,
            db=db,
            settings=settings,
            job_deliverer=goal_job_deliverer,
        )
        HandlerRegistry.instance().register(goal_execution_handler)

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
            critic_scorer_handler=critic_scorer_handler,
            reflection_writer_handler=reflection_writer_handler,
            skill_synthesizer_handler=skill_synthesizer_handler,
            tool_outcome_miner_handler=tool_outcome_miner_handler,
        )


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
