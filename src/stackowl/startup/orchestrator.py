"""StartupOrchestrator — 5-phase boot sequence with PID file and dry-run support."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import signal
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from stackowl.config.settings import Settings
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool, default_db_path
from stackowl.exceptions import ConfigurationError, StartupError
from stackowl.paths import StackowlHome
from stackowl.service.watchdog import WatchdogService
from stackowl.startup.browser_probe import BrowserProbe, BrowserProbeResult
from stackowl.startup.fs_probe import FilesystemProbe
from stackowl.startup.provider_probe import ProviderProbe

log = logging.getLogger("stackowl.startup")


def resolve_reply_to_inflight(*, is_reply: bool, turn_running: bool) -> bool:
    """Map a channel reply-to-the-bot flag to a STRUCTURAL reply-to-inflight STEER.

    STEER-1/F060: ``IngressMessage.is_reply`` is True when the inbound message is
    a channel reply to one of the bot's own messages (Telegram stamps it from
    ``message.reply_to_message``). It becomes a reply-to-inflight STEER — the
    structural, zero-LLM-cost signal that ``parse_explicit_signal`` honours — ONLY
    when a turn is ACTUALLY in-flight for the session. A reply to an OLD bot
    message with nothing running is just a normal message (NOT a steer), so it
    must NOT short-circuit into the mid-turn router. Pure, side-effect-free; the
    caller already gates on ``running_turn is not None`` but we make the contract
    explicit and unit-testable here.
    """
    return bool(is_reply and turn_running)


async def _run_until_signal(adapter: object, stop_event: asyncio.Event) -> None:
    """Race the blocking ``adapter.run()`` against a cooperative ``stop_event``.

    Returns as soon as EITHER the adapter exits on its own (CLI user quits) OR a
    signal sets ``stop_event`` (SIGTERM/SIGINT). The loser is cancelled and awaited
    so the caller's ``finally`` runs the real graceful teardown — replacing the old
    ``SystemExit`` hard-raise that bypassed it (F144). Pure structural helper:
    never raises out (CancelledError on the loser is suppressed)."""
    log.debug("[startup] _run_until_signal: entry")
    adapter_task = asyncio.ensure_future(adapter.run())  # type: ignore[attr-defined]
    stop_task = asyncio.ensure_future(stop_event.wait())
    try:
        done, pending = await asyncio.wait(
            {adapter_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        via_signal = stop_task in done
        log.info(
            "[startup] _run_until_signal: woke — via_signal=%s",
            via_signal,
            extra={"_fields": {"via_signal": via_signal}},
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        # Re-raise a genuine adapter error (not a cancellation) so the caller's
        # phase-6 wrapper records it; a clean signal exit just returns.
        if adapter_task in done and not adapter_task.cancelled():
            exc = adapter_task.exception()
            if exc is not None:
                raise exc
    finally:
        log.debug("[startup] _run_until_signal: exit")

# Long-lived strong refs for fire-and-forget completion-drain tasks so the loop
# can't GC them mid-flight (asyncio only weakly references tasks). Each task
# discards itself on completion via add_done_callback below.
_drain_tasks: set[asyncio.Task[object]] = set()

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.pipeline.streaming import ResponseChunk


class _IntakeAdapter(Protocol):
    """The channel-adapter surface the §4.3 intake helpers need.

    Both the CLI and Telegram adapters satisfy this: ``send`` drains a turn's
    response stream, ``send_text`` posts the instant queued-intake ack, and
    ``channel_name`` lets the §9-inv.1 completion seam stamp a re-routed survivor
    steer's synthetic IngressMessage with the SAME channel it arrived on.
    """

    @property
    def channel_name(self) -> str: ...  # noqa: D102

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None: ...  # noqa: D102

    async def send_text(self, text: str) -> None: ...  # noqa: D102


def _build_heuristic_store(db_pool):  # type: ignore[no-untyped-def]
    """Build the ToolHeuristicStore used by post-tool heuristic emission."""
    from stackowl.learning.tool_heuristic_store import ToolHeuristicStore

    return ToolHeuristicStore(db_pool)


def _log_pipeline_crash(task: asyncio.Task) -> None:  # type: ignore[type-arg]
    """Done-callback that surfaces unhandled pipeline task exceptions to the log."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error(
            "[startup] pipeline task crashed — unhandled exception",
            exc_info=exc,
            extra={"_fields": {"task_name": task.get_name()}},
        )


class _SlackMemoryBridgeComposite:
    """Bridge facade exposing BOTH ``force_promote`` and ``delete`` for Slack memory taps.

    The ``SlackMemoryActionHandler`` (Slack B2) needs a single bridge object that
    can both PROMOTE an approved fact (``force_promote``, which lives on
    :class:`~stackowl.memory.fact_promoter.FactPromoter`) and DELETE a rejected
    one (``delete``, which lives on the :class:`~stackowl.memory.bridge.MemoryBridge`).
    Neither object exposes both, so this composite delegates each operation to the
    component that owns it — ``force_promote`` → the promoter (the approve path),
    everything else (``delete``, and any other bridge call) → the bridge. Keeping
    this a thin delegator (not a subclass) avoids fabricating a fake bridge while
    giving the handler the exact two-method surface it probes for.
    """

    def __init__(self, *, bridge: object, promoter: object) -> None:
        self._bridge = bridge
        self._promoter = promoter

    async def force_promote(self, fact_id: str) -> bool:
        """Promote an approved staged fact via the FactPromoter (the approve path)."""
        log.debug(
            "[startup] slack memory composite: force_promote",
            extra={"_fields": {"fact_id": fact_id}},
        )
        return await self._promoter.force_promote(fact_id)  # type: ignore[attr-defined,no-any-return]

    async def delete(self, fact_id: str) -> None:
        """Delete a rejected staged fact via the bridge (the reject path)."""
        log.debug(
            "[startup] slack memory composite: delete",
            extra={"_fields": {"fact_id": fact_id}},
        )
        await self._bridge.delete(fact_id)  # type: ignore[attr-defined]


def _pid_path() -> Path:
    return StackowlHome.pid_file()


class StartupOrchestrator:
    """Boots StackOwl through 6 named phases; raises StartupError on any failure."""

    def __init__(self, dry_run: bool = False) -> None:
        self._dry_run = dry_run
        self._settings: Settings | None = None
        self._browser_probe_result: BrowserProbeResult | None = None
        self._shutting_down = False  # F144 — idempotency guard for double-signal
        self._migrations_applied = False  # F146 — single migration site per boot

    def ensure_migrations(self) -> None:
        """Apply pending migrations exactly once per orchestrator instance (F146).

        This is the SINGLE migration site shared by both ``start`` and ``serve``.
        ``cli start`` calls it directly before its first-run/onboarding detection
        (which needs the schema) using the SAME orchestrator instance it later
        passes to :meth:`run`; ``_phase_migrations`` (boot phase 1) also routes
        through here. The idempotency flag means a boot migrates exactly once
        regardless of how many callers ask — replacing the old double-run where
        ``cli start`` migrated and then the orchestrator migrated again."""
        log.debug("[startup] ensure_migrations: entry applied=%s", self._migrations_applied)
        if self._dry_run:
            log.info("[startup] ensure_migrations: dry_run — skipping migration application")
            return
        if self._migrations_applied:
            log.debug("[startup] ensure_migrations: already applied this boot — skipping")
            return
        db_path = default_db_path()
        MigrationRunner(db_path=db_path).run()
        self._migrations_applied = True
        log.info("[startup] ensure_migrations: exit — migrations applied")

    async def run(self) -> None:
        log.info("[startup] orchestrator.run: entry dry_run=%s", self._dry_run)
        self._settings = Settings()

        # Phases 1-5: non-blocking setup
        setup_phases: list[tuple[int, str, object]] = [
            (1, "migrations", self._phase_migrations),
            (2, "filesystem", self._phase_filesystem),
            (3, "reconciler", self._phase_reconciler),
            (4, "providers", self._phase_providers),
            (5, "browser_install", self._phase_browser_install),
        ]
        for num, name, fn in setup_phases:
            t0 = time.monotonic()
            log.info("[startup] phase %d (%s): start", num, name)
            try:
                await fn()  # type: ignore[operator]
            except StartupError:
                raise
            except Exception as exc:
                log.error("[startup] phase %d (%s): FAILED", num, name, exc_info=exc)
                raise StartupError(num, name, str(exc)) from exc
            log.info("[startup] phase %d (%s): ok (%.0fms)", num, name, (time.monotonic() - t0) * 1000)

        # Write PID before the blocking gateway phase. The systemd watchdog +
        # READY=1 are now driven by the real WatchdogService INSIDE _phase_gateway
        # (F142): pinging must be recurring, and READY must fire only after the
        # service is actually serving-ready — not here, before the gateway assembles.
        if not self._dry_run:
            self._write_pid()

        # Phase 6 — gateway: blocks until the user exits (no-op in dry_run)
        log.info("[startup] phase 6 (gateway): start")
        t0 = time.monotonic()
        try:
            await self._phase_gateway()
        except StartupError:
            raise
        except Exception as exc:
            log.error("[startup] phase 6 (gateway): FAILED", exc_info=exc)
            raise StartupError(6, "gateway", str(exc)) from exc
        log.info("[startup] phase 6 (gateway): ok (%.0fms)", (time.monotonic() - t0) * 1000)

        log.info("[startup] orchestrator.run: exit — ready")

    async def _phase_migrations(self) -> None:
        # F146 — route through the single idempotent migration site so a boot
        # migrates exactly once even if cli start already applied them.
        self.ensure_migrations()

    async def _phase_filesystem(self) -> None:
        FilesystemProbe().check(dry_run=self._dry_run)

    async def _phase_reconciler(self) -> None:
        log.info("[startup] reconciler: ok — no agents to reconcile")

    async def _phase_providers(self) -> None:
        assert self._settings is not None
        providers = self._settings.providers
        if not providers:
            log.info("[startup] providers: no providers configured — skipping probe")
            return
        probe = ProviderProbe(providers)
        await probe.check()

    async def _phase_browser_install(self) -> None:
        """Auto-install the Camoufox browser binary if missing.

        Never raises — a failure here degrades browser tools to 'unavailable'
        rather than blocking startup.
        """
        if self._dry_run:
            log.info("[startup] browser_install: dry_run — skipping probe and fetch")
            return
        probe = BrowserProbe()
        self._browser_probe_result = await probe.check(fetch_if_missing=True)
        if not self._browser_probe_result.ready:
            log.warning(
                "[startup] browser_install: not ready — browser tools will be unavailable (%s)",
                self._browser_probe_result.error,
            )

    async def _phase_gateway(self) -> None:
        """Start channel adapters and run the main message loop.

        Blocks until the CLI adapter exits (user closes the TUI).
        In dry_run mode, returns immediately after logging.
        """
        if self._dry_run:
            log.info("[startup] gateway: dry_run — skipping adapter start")
            return

        from stackowl.audit.logger import AuditLogger
        from stackowl.channels.cli_adapter import CLIAdapter
        from stackowl.commands.registry import CommandRegistry
        from stackowl.exceptions import CommandNotFoundError
        from stackowl.gateway.scanner import GatewayScanner
        from stackowl.owls.registry import OwlRegistry
        from stackowl.parliament.orchestrator import ParliamentOrchestrator
        from stackowl.parliament.session_store import SessionStore
        from stackowl.pipeline.backends.factory import create_backend
        from stackowl.pipeline.services import StepServices
        from stackowl.pipeline.state import PipelineState
        from stackowl.pipeline.streaming import ResponseChunk, StreamRegistry
        from stackowl.providers.registry import ProviderRegistry
        from stackowl.tools.browser.runtime import CamoufoxRuntime
        from stackowl.tools.browser.sessions import BrowserSessionRegistry
        from stackowl.tools.registry import ToolRegistry

        assert self._settings is not None

        # 1. ENTRY — build services
        log.info("[startup] gateway: building services")
        provider_registry = ProviderRegistry.from_settings(self._settings)
        stream_registry = StreamRegistry()
        # §4.3 non-blocking in-chat intake: one process-wide TurnRegistry SHARED by
        # every channel loop (CLI + Telegram). Per session it tracks at most one
        # RUNNING turn + a FIFO intake queue, so a same-session message that
        # arrives mid-turn is enqueued (not blocked) and dispatched on completion;
        # cross-session turns stay fully parallel. This subsumes the deleted
        # serialize_prior gate.
        from stackowl.gateway.turn_registry import QueueFull, TurnRegistry

        turn_registry = TurnRegistry()
        owl_registry = OwlRegistry.from_settings(self._settings)
        owl_registry.register_builtin_personas()
        db_pool = DbPool(default_db_path())
        await db_pool.open()
        from stackowl.owls.dna_authored import capture_authored_dna

        await capture_authored_dna(owl_registry, db_pool)
        from stackowl.owls.dna_hydrator import hydrate_dna

        await hydrate_dna(owl_registry, db_pool)
        from stackowl.owls.owl_revalidator import revalidate_agent_owls

        revalidate_agent_owls(owl_registry)
        # Memory subsystem assembly — wires the entire consolidation stack
        # (bridge, preference store, Kuzu adapter, DreamWorker, FactExtractor)
        # via the MemoryAssembly factory. See plan: gleaming-finding-puppy.md
        # Commit A. Hard-fails on Kuzu init per operator-approved decision.
        from stackowl.memory.assembly import MemoryAssembly

        memory_components = await MemoryAssembly.build(
            db=db_pool, settings=self._settings, provider_registry=provider_registry,
        )
        memory_bridge = memory_components.bridge
        preference_store = memory_components.preference_store
        kuzu_adapter = memory_components.kuzu_adapter
        tool_registry = ToolRegistry.with_defaults()
        # Skills subsystem — unified Skill workspace (builtin/installed/user/learned).
        # Seeds shipped builtins from src/stackowl/skills/_builtin/ on every boot,
        # scans all four source dirs under ~/.stackowl/workspace/skills/, registers
        # each skill's tools/owls extensions into the running registries, caches
        # manifests in SQLite for fast retrieval. See plan gleaming-finding-puppy.md
        # Learning Commit 3, sub-phase 3a.
        from stackowl.skills.assembly import SkillsAssembly

        skills_components = await SkillsAssembly.build(
            db=db_pool, tool_registry=tool_registry, owl_registry=owl_registry,
            embedding_registry=memory_components.embedding_registry,
            lessons_index=memory_components.lessons_index,
            provider_registry=provider_registry,
        )
        log.info(
            "[startup] gateway: skills loaded",
            extra={"_fields": {"count": len(skills_components.loaded)}},
        )

        # H4 — reload agent-authored (learned) tools so a tool the agent minted via
        # tool_build survives reboots. Runs AFTER with_defaults + SkillsAssembly so
        # the built-ins are already registered: the collision/dangerous-shadow guard
        # then protects them (a learned spec can never clobber a built-in). Reads
        # declarative *.json specs only — NEVER execs model-authored Python.
        from stackowl.tools.meta.learned_tool_loader import LearnedToolLoader

        learned_count = await LearnedToolLoader().load_all(tool_registry)
        log.info(
            "[startup] gateway: learned tools loaded",
            extra={"_fields": {"count": learned_count}},
        )

        # E1-S3 — MCP federation boot phase (after providers/skills, before traffic).
        # Fail-soft: a down/slow/misconfigured server never blocks boot. Federated
        # tools register namespaced (mcp.<server>.<tool>), non-clobbering.
        mcp_cfg = self._settings.mcp_client
        if mcp_cfg.auto_discover_on_startup and mcp_cfg.servers:
            try:
                from stackowl.mcp.allowlist import McpServerAllowlist
                from stackowl.mcp.cache import McpToolCache
                from stackowl.mcp.client import McpClient
                from stackowl.mcp.probe import McpLivenessProbe
                from stackowl.startup.mcp_register import run as mcp_register_run

                mcp_client = McpClient(
                    McpServerAllowlist(list(mcp_cfg.allowed_uri_prefixes)),
                    McpToolCache(ttl_seconds=mcp_cfg.tool_cache_ttl_seconds),
                    McpLivenessProbe(),
                )
                mcp_summary = await mcp_register_run(mcp_client, list(mcp_cfg.servers), tool_registry)
                log.info(
                    "[startup] gateway: MCP tools registered",
                    extra={"_fields": {"servers": len(mcp_summary), "tools": sum(mcp_summary.values())}},
                )
            except Exception as exc:  # boot must survive a broken MCP layer
                log.error(
                    "[startup] gateway: MCP registration phase failed — continuing without federation",
                    exc_info=exc,
                )
        audit_logger = AuditLogger(default_db_path())

        # Notifications subsystem assembly — router singleton + scheduled
        # digest job. Focus mode persists across restarts via PreferenceStore.
        # NOTE: router-dependent commands are registered below via
        # register_all_commands once router exists in CommandDeps.
        from stackowl.events.bus import EventBus
        from stackowl.notifications.assembly import NotificationAssembly

        event_bus = EventBus()

        notification_components = await NotificationAssembly.build(
            db=db_pool,
            settings=self._settings,
            event_bus=event_bus,
            preference_store=preference_store,
        )
        notification_router = notification_components.router
        proactive_deliverer = notification_components.proactive_deliverer


        # Browser runtime — only start if the binary is present (libs/xvfb are advisory).
        browser_runtime: CamoufoxRuntime | None = None
        browser_sessions: BrowserSessionRegistry | None = None
        probe = self._browser_probe_result
        if probe is not None and probe.binary_ok:
            browser_settings = self._settings.browser
            # Auto-degrade headless='virtual' → 'true' when Xvfb is missing.
            if browser_settings.headless_mode == "virtual" and not probe.xvfb_ok:
                log.warning(
                    "[startup] gateway: Xvfb missing — degrading headless_mode 'virtual' → 'true'"
                )
                browser_settings = browser_settings.model_copy(update={"headless_mode": "true"})
            browser_runtime = CamoufoxRuntime(browser_settings)
            await browser_runtime.start()
            if not browser_runtime.available:
                log.warning(
                    "[startup] gateway: browser runtime failed to start — tools will be unavailable (%s)",
                    browser_runtime.unavailable_reason,
                )
                browser_runtime = None
            else:
                browser_sessions = BrowserSessionRegistry(browser_runtime, browser_settings)
                await browser_sessions.start_sweep_loop()
                # Register browser-dependent scheduler handlers now that runtime is up.
                from stackowl.scheduler.handlers.browser_cache_eviction import (
                    register_browser_cache_eviction_handler,
                )
                from stackowl.scheduler.handlers.browser_recycle import register_browser_recycle_handler
                from stackowl.scheduler.handlers.credential_rotation import (
                    register_credential_rotation_handler,
                )
                from stackowl.scheduler.handlers.profile_backup import register_profile_backup_handler
                from stackowl.scheduler.handlers.screenshot_archive import (
                    register_screenshot_archive_handler,
                )
                from stackowl.scheduler.handlers.website_watch import register_website_watch_handler

                watch_state_dir = browser_settings.browser_cache_dir / "watch"
                screenshot_archive_dir = StackowlHome.knowledge_dir() / "screenshots"
                profile_backups_dir = StackowlHome.home() / "backups" / "browser-profiles"
                register_website_watch_handler(browser_runtime, watch_state_dir)
                register_screenshot_archive_handler(browser_runtime, screenshot_archive_dir)
                register_browser_recycle_handler(browser_runtime, browser_sessions)
                register_browser_cache_eviction_handler(
                    browser_settings.browser_cache_dir, browser_settings.screenshots_dir,
                )
                register_credential_rotation_handler(
                    browser_runtime, browser_settings.browser_cache_dir / "credential_rotation",
                )
                register_profile_backup_handler(browser_settings.profiles_dir, profile_backups_dir)
        else:
            reason = "binary not found" if probe is not None else "probe did not run"
            log.warning("[startup] gateway: browser runtime skipped — %s", reason)

        # E0-S1 — consent gate: combination consent policy + per-channel prompters.
        # Routing prompter is mutable so the Telegram prompter can register after
        # its adapter starts (below). CLI gets the TTY prompter immediately.
        # Wired via the ConsentAssembly seam (OPS-5/F149) — extracted from this
        # monolith so the consent boundary has a unit-testable assembly.
        from stackowl.tools.consent_assembly import ConsentAssembly

        consent_components = ConsentAssembly.build(audit_logger)
        consent_routing = consent_components.routing_prompter
        consent_gate = consent_components.consent_gate

        # E5 — clarify pause/resume gateway. One DI singleton: tools reach it via
        # get_services().clarify_gateway to ask the user mid-turn; the message
        # loops reach it (closure) to resolve a reply into the parked turn. Per
        # channel adapters register themselves below so the gateway can deliver.
        from stackowl.interaction.clarify_gateway import ClarifyGateway
        from stackowl.interaction.intent_classifier import ClarifyIntentClassifier

        clarify_gateway = ClarifyGateway()
        # Classifies a during-park typed reply as answer vs new-request (fast tier,
        # fail-safe→answer) so a user who pivots isn't silently answered with their
        # unrelated message. The clarify pumps consult it before resolving.
        clarify_classifier = ClarifyIntentClassifier(provider_registry)
        # §6/§7 (P3 Task 16) — the mid-turn arrival TurnRouter. Reuses the SAME
        # fast-tier ``ClarifyIntentClassifier`` (its ``is_steer`` is the
        # conservative high-confidence STEER-vs-NEW propose stage), so there is ONE
        # classifier instance for both the clarify answer-vs-new path and the
        # steer-vs-new path (DRY; no second provider wiring). The stage-2 coherence
        # veto (concurrent-msg §5.5) is now wired: ``turn_veto`` is the SAME
        # classifier's ``is_steer_incoherent`` — a DISTINCT LLM judgment (the running
        # turn's OWN coherence check) that asks whether folding a proposed steer
        # would blend INCOHERENTLY / CONTRADICT the running goal (→ veto → NEW),
        # rather than ``is_steer``'s refinement-vs-new propose question. It fail-safes
        # to VETO (the safe direction — a wrong veto only yields a separate coherent
        # answer; a wrong non-veto risks an incoherent blend). The router consults it
        # only on a PROPOSED steer; its callable signature ``(running_ask=, message=)``
        # matches what ``TurnRouter.route`` passes the veto.
        from stackowl.gateway.turn_router import ExplicitSignal, TurnRouter

        turn_router = TurnRouter(
            clarify_classifier, turn_veto=clarify_classifier.is_steer_incoherent,
        )
        # Periodic reaper for abandoned turn-yield clarify entries (blocking ones
        # self-reap via their own park timeout). Recurring job seeded in the
        # scheduler assembly ("clarify_sweep", every 30m).
        from stackowl.scheduler.handlers.clarify_sweep import (
            register_clarify_sweep_handler,
        )

        register_clarify_sweep_handler(clarify_gateway)

        # E6 — web-search provider registry (precedence: SearXNG → Brave → DDG).
        # SearXNG/Brave are configured upgrades; DDG is the keyless zero-config floor.
        # brave_api_key is a SECRET REFERENCE string (env-var / keychain:/ file:); ""
        # disables the Brave provider. The web_search tool reads this off services.
        from stackowl.web_search.providers import BraveProvider, DdgProvider, SearxngProvider
        from stackowl.web_search.registry import WebSearchRegistry

        web_search_registry = WebSearchRegistry(
            [
                SearxngProvider(self._settings.web_search.searxng_base_url),
                BraveProvider(self._settings.web_search.brave_api_key or None),
                DdgProvider(),
            ]
        )

        # E8-S0 — ONE shared concurrency governor: bounds total in-flight
        # delegated + parliament pipelines on this host. Injected onto
        # StepServices (A2ADelegator reads it off services) AND into the
        # ParliamentOrchestrator below, so both draw from a SINGLE budget.
        from stackowl.messaging.a2a import A2AQueue
        from stackowl.owls.concurrency import ConcurrencyGovernor

        delegation_governor = ConcurrencyGovernor()
        # E8-S1 — ONE A2A mailbox shared by the dispatch step + A2ADelegator so a
        # specialist's reply lands in the same queue the caller awaits on.
        a2a_queue = A2AQueue()

        # E8-S3 — the named-session registry. ONE DI singleton, sharing the SAME
        # a2a_queue so a cleared/reaped session drains the right mailbox. The
        # recurring TTL sweep is registered as a JobHandler below and seeded as a
        # `session_sweep` job row in the scheduler assembly (every 10m). Wired
        # onto StepServices so the sessions_spawn tool reaches THIS instance.
        from stackowl.owls.session_registry import SessionRegistry
        from stackowl.scheduler.handlers.session_sweep import (
            register_session_sweep_handler,
        )

        session_registry = SessionRegistry(a2a_queue=a2a_queue)
        register_session_sweep_handler(session_registry)

        # E9-S0 — the process substrate. ONE DI singleton owning supervised OS-
        # process lifecycle (bounded concurrency + a MANDATORY max lifetime,
        # captured stdout/stderr, its OWN checkpoint). Clock-injected (ARCH-99) so
        # the TTL sweep is deterministically testable. `reconcile()` at boot probes
        # any pids the previous run checkpointed — re-adopting the alive ones and
        # recording the dead. The recurring TTL/prune sweep is registered as a
        # JobHandler here and seeded as a `process_sweep` job row in the scheduler
        # assembly (every 10m). Wired onto StepServices so the (S1) process tool
        # reaches THIS instance. clear_all() on shutdown terminates every process.
        from stackowl.process.registry import ProcessRegistry
        from stackowl.scheduler.handlers.process_sweep import (
            register_process_sweep_handler,
        )

        process_registry = ProcessRegistry()
        process_registry.reconcile()
        register_process_sweep_handler(process_registry)
        # F050 — register the turn-sweep backstop reaper next to process_sweep. The
        # recurring JOB row is seeded in scheduler assembly (every 10m << TTL). The
        # stranded-session drain callback is wired below, once the pump + adapter
        # the drain seam needs are in scope.
        from stackowl.scheduler.handlers.turn_sweep import register_turn_sweep_handler

        register_turn_sweep_handler(turn_registry)

        # E11-S5 — the sandbox backend selector (the KEYSTONE code-execution trust
        # boundary). ONE DI singleton built from settings.sandbox: the rootless
        # bwrap backend (PRIMARY) + the network-capable Docker backend, each gated
        # by its enabled flag (a disabled backend reports unavailable and is never
        # picked). The execute_code tool reads THIS off services at execute time; if
        # neither backend is viable the selector returns a structured unavailable and
        # the tool NEVER runs code on the host. Wired onto StepServices below.
        # Wired via the SandboxAssembly seam (OPS-5/F149) — extracted from this
        # monolith so the code-execution trust boundary has a unit-testable
        # assembly. Builds the selector + the shared SandboxGovernor (bounds total
        # concurrent runs so N runs × the per-run memory cap can't OOM the host)
        # and registers the recurring sandbox_sweep GC handler.
        from stackowl.sandbox.assembly import SandboxAssembly

        sandbox_components = SandboxAssembly.build(self._settings)
        sandbox_selector = sandbox_components.selector
        sandbox_governor = sandbox_components.governor

        # E8-S0cost — ONE shared CostTracker (per-turn running total feeds the soft
        # cost-pause) + the CostPauseGuard that asks the user "Continue?" via the
        # clarify gateway before the next expensive op once a turn crosses the soft
        # per-turn budget. The daily hard cap stays on this same tracker. The guard
        # fails OPEN (never wedges a turn) and is interactive-only.
        from stackowl.interaction.cost_pause import CostPauseGuard
        from stackowl.providers.cost_tracker import CostTracker
        from stackowl.providers.pricing.loader import PricingLoader

        cost_tracker = CostTracker(
            db=db_pool,
            event_bus=event_bus,
            daily_limit_usd=self._settings.budget.daily_limit_usd,
            # F128 — seed the loader's conservative fallback for unknown CLOUD models
            # from config so an unpriced paid model trips the budget, not bills $0.
            pricing=PricingLoader(
                unknown_cloud_per_1m_usd=self._settings.budget.unknown_cloud_per_1m_usd,
            ),
        )
        # E8-S0cost — make providers the SINGLE cost-recording site: inject the ONE
        # shared tracker into every provider so a turn's REAL main-pipeline spend
        # (complete + each tool-loop API round) feeds CostTracker.turn_cost_usd and
        # the soft cost-pause can fire. Done AFTER both exist (the tracker needs
        # db_pool/event_bus built later than the registry). The router + MoA call
        # provider.complete, so they no longer record separately (no double-count).
        provider_registry.set_cost_tracker(cost_tracker)

        # LIVE provider hot-reload — when stackowl.yaml changes (e.g. via the
        # /provider command), pick up provider add/remove/change WITHOUT a restart.
        # The ConfigWatcher polls the yaml on a daemon thread and emits
        # `settings_reloaded` with the new Settings; the handler mutates the SAME
        # registry object in place (callers captured this reference at startup).
        # Gated by `settings_watch`. Started before the blocking adapter await and
        # stopped in the `finally` block (so it never violates gateway-must-block).
        # F105 — event-driven proactivity: subscribe the EventDeliveryBridge for
        # the allow-listed proactive events, funnelling each through the SAME
        # proactive_deliverer.deliver seam the cron path uses (no parallel path).
        # Registered once here next to the settings_reloaded subscribe.
        from stackowl.notifications.event_bridge import EventDeliveryBridge

        EventDeliveryBridge(deliverer=proactive_deliverer).register(event_bus)
        log.info("[startup] gateway: event-driven proactivity bridge registered")

        config_watcher = None
        if self._settings.settings_watch:
            from stackowl.config.watcher import ConfigWatcher
            from stackowl.startup.provider_reload import make_settings_reload_handler

            event_bus.subscribe(
                "settings_reloaded", make_settings_reload_handler(provider_registry)
            )
            config_watcher = ConfigWatcher(
                config_path=StackowlHome.config_file(),
                event_bus=event_bus,
                settings_factory=lambda: Settings(),
            )
            config_watcher.start()
            log.info("[startup] gateway: config watcher started (live provider reload)")

        cost_pause_guard = CostPauseGuard(
            cost_tracker=cost_tracker,
            clarify_gateway=clarify_gateway,
            threshold_usd=self._settings.budget.per_turn_pause_usd,
        )

        services = StepServices(
            a2a_queue=a2a_queue,
            provider_registry=provider_registry,
            stream_registry=stream_registry,
            owl_registry=owl_registry,
            memory_bridge=memory_bridge,
            kuzu_adapter=kuzu_adapter,
            tool_registry=tool_registry,
            db_pool=db_pool,
            browser_runtime=browser_runtime,
            browser_sessions=browser_sessions,
            audit_logger=audit_logger,
            preference_store=preference_store,
            notification_router=notification_router,
            proactive_deliverer=proactive_deliverer,
            event_bus=event_bus,
            skill_store=skills_components.store,
            embedding_registry=memory_components.embedding_registry,
            lessons_index=memory_components.lessons_index,
            heuristic_store=_build_heuristic_store(db_pool),
            consent_gate=consent_gate,
            clarify_gateway=clarify_gateway,
            web_search_registry=web_search_registry,
            delegation_governor=delegation_governor,
            session_registry=session_registry,
            process_registry=process_registry,
            cost_tracker=cost_tracker,
            cost_pause_guard=cost_pause_guard,
            sandbox_selector=sandbox_selector,
            sandbox_governor=sandbox_governor,
            turn_registry=turn_registry,
            settings=self._settings,
        )
        # E8-S1 — construct the SINGLE A2ADelegator AFTER services exists (it reads
        # the shared governor + a2a_queue off services), then inject it back onto
        # the same mutable StepServices so the delegate_task tool reaches THIS
        # instance. No second delegator with a different governor/queue is created.
        from stackowl.owls.a2a_delegation import A2ADelegator

        services.a2a_delegator = A2ADelegator(a2a_queue=a2a_queue, services=services)
        backend = create_backend(self._settings.orchestrator.backend, services=services)
        parliament_session_store = SessionStore(db_pool)
        parliament = ParliamentOrchestrator(
            backend=backend,
            session_store=parliament_session_store,
            delegation_governor=delegation_governor,
        )
        scanner = GatewayScanner(owl_registry=owl_registry)

        # Scheduler subsystem assembly — JobScheduler + Supervisor + the 6
        # previously-orphaned handlers. Without this nothing in jobs table
        # ever dispatches (browser handlers, dream worker, fact extraction,
        # notification digest, etc. all depended on the scheduler loop).
        # See plan gleaming-finding-puppy.md Commit E.
        from stackowl.scheduler.assembly import SchedulerAssembly

        scheduler_components = await SchedulerAssembly.build(
            db=db_pool,
            settings=self._settings,
            event_bus=event_bus,
            provider_registry=provider_registry,
            owl_registry=owl_registry,
            memory_components=memory_components,
            backend=backend,
            skills_components=skills_components,
            proactive_deliverer=proactive_deliverer,
            # PARL-7 (F084) — the host-wide governor so the nightly evolution
            # batch's concurrent fan-out shares the single in-flight budget.
            delegation_governor=delegation_governor,
        )

        # Single registration point for ALL slash commands (Epic A spine).
        # Must run AFTER SchedulerAssembly.build() so morning_brief_handler
        # and scheduler are available. See Epic B batch-1.
        from stackowl.commands.assembly import CommandDeps, register_all_commands
        from stackowl.integrations.registry import IntegrationRegistry
        from stackowl.plugins.registry import PluginRegistry

        # Cooperative shutdown event — created here so /bye can trip it; the
        # signal handlers below (SIGTERM/SIGINT) set the SAME event.
        stop_event = asyncio.Event()
        register_all_commands(CommandDeps(
            event_bus=event_bus,
            db=db_pool,
            router=notification_router,
            settings=self._settings,
            owl_registry=owl_registry,
            tool_registry=tool_registry,
            bridge=memory_bridge,
            lancedb=memory_components.lancedb,
            promoter=memory_components.promoter,
            embedding_registry=memory_components.embedding_registry,
            skills_store=skills_components.store,
            skills_loader=skills_components.loader,
            skills_root=StackowlHome.skills_dir(),
            audit_logger=audit_logger,
            parliament_orchestrator=parliament,
            scheduler=scheduler_components.scheduler,
            morning_brief_handler=scheduler_components.morning_brief_handler,
            preference_store=preference_store,
            plugin_registry=PluginRegistry(default_db_path()),
            integration_registry=IntegrationRegistry.instance(),
            provider_registry=provider_registry,
            parliament_session_store=parliament_session_store,
            shutdown_event=stop_event,
        ))

        # Plugin index — discover installed plugins from ~/.stackowl/plugins/.
        # Failures log a warning but do not abort the gateway phase.
        try:
            from stackowl.plugins.index import PluginIndex

            plugin_index = PluginIndex()  # auto-loads on construction
            log.info(
                "[startup] gateway: plugin index loaded",
                extra={"_fields": {"count": len(plugin_index.all())}},
            )
        except Exception as exc:
            log.warning(
                "[startup] gateway: plugin index load failed — continuing without plugins",
                exc_info=exc,
            )

        # MCP server auto-start — opt-in via mcp_server.enabled=True.
        # The server runs as a background task; clients connect via configured
        # transport (sse/stdio). See plan Commit E (operator-approved opt-in).
        mcp_task: asyncio.Task[None] | None = None
        if self._settings.mcp_server.enabled:
            try:
                from stackowl.mcp.server import McpServer

                mcp_server = McpServer(
                    tool_registry=tool_registry,
                    settings=self._settings.mcp_server,
                    global_settings=self._settings,
                    event_bus=event_bus,
                )
                # Pick transport per settings — sse is non-blocking server,
                # stdio blocks awaiting client. stdio not viable here since
                # CLI adapter already owns stdin; auto-start only sse.
                if self._settings.mcp_server.transport in ("sse", "both"):
                    mcp_task = asyncio.create_task(mcp_server.start_sse())
                    log.info(
                        "[startup] gateway: MCP server auto-start scheduled (sse)",
                        extra={"_fields": {
                            "port": self._settings.mcp_server.port,
                        }},
                    )
                else:
                    log.warning(
                        "[startup] gateway: MCP auto-start skipped — stdio transport "
                        "conflicts with CLI adapter; use `stackowl mcp start` instead",
                    )
            except Exception as exc:
                log.warning(
                    "[startup] gateway: MCP auto-start failed — continuing without MCP",
                    exc_info=exc,
                )
        else:
            log.debug("[startup] gateway: MCP auto-start disabled (mcp_server.enabled=False)")

        # TUI assembly — 4-zone Textual app + UIStateCoordinator.
        # See plan gleaming-finding-puppy.md Commit D. CLIAdapter takes the
        # assembled components and routes input/output through the EventBus
        # singleton already wired in Commit C.
        from stackowl.tui.assembly import TuiAssembly
        from stackowl.tui.widgets.compose_helpers import CommandInfo

        _commands = CommandRegistry.instance().list()
        command_names = [c.command for c in _commands]
        command_infos = [
            CommandInfo(name=c.command, description=c.description) for c in _commands
        ]
        owl_names = [m.name for m in owl_registry.list()]
        tui_components = TuiAssembly.build(
            event_bus=event_bus,
            command_names=command_names,
            command_infos=command_infos,
            owl_names=owl_names,
            ui_settings=self._settings.ui,
        )
        adapter = CLIAdapter(
            tui_components=tui_components, event_bus=event_bus,
        )
        # E5 — let the clarify gateway deliver questions back over the CLI.
        clarify_gateway.register_adapter("cli", adapter)

        # 2. DECISION — define the message processing loop
        async def _deliver_parliament(
            topic: str, owl_names: list[str], session_id: str, trace_id: str,
        ) -> None:
            """Run parliament and deliver the synthesis to the stream writer."""
            try:
                session = await parliament.run(topic=topic, owl_names=owl_names, session_id=session_id)
                synthesis = session.synthesis or "Parliament session completed with no synthesis."
            except Exception as exc:
                log.error("[startup] gateway: parliament session failed", exc_info=exc)
                synthesis = f"Parliament error: {exc}"
            # §4.1 stream re-key: the response stream is keyed by trace_id (the key
            # deliver resolves by). Fetch + write the chunk under that same key.
            writer = stream_registry.get_writer(trace_id)
            if writer is not None:
                await writer.write(ResponseChunk(
                    content=synthesis, is_final=False, chunk_index=0,
                    trace_id=trace_id, owl_name="parliament",
                ))
                await writer.close()

        async def _deliver_command_stub(
            cmd: str, session_id: str, state: PipelineState, args: str, trace_id: str,
        ) -> None:
            """Dispatch a slash command and stream its reply back to the user."""
            registry = CommandRegistry.instance()
            # §4.1 stream re-key: fetch the writer under the stream key (trace_id).
            writer = stream_registry.get_writer(trace_id)
            try:
                reply = await registry.dispatch(cmd, args, state)
            except CommandNotFoundError:
                reply = f"Unknown slash command: '/{cmd}'. Try /help to see what's available."
            except Exception as exc:
                log.error("[startup] gateway: slash command failed", exc_info=exc)
                reply = f"Command '/{cmd}' failed: {exc}"
            if writer is not None:
                await writer.write(ResponseChunk(
                    content=reply, is_final=False, chunk_index=0,
                    trace_id=trace_id, owl_name="system",
                ))
                await writer.close()

        # E5 — clarify-aware turn dispatch. Each channel loop owns a ClarifyPump
        # (its own in-flight map): it decouples adapter.send from receive so a
        # parked clarify turn doesn't deadlock the loop, intercepts replies into
        # their parked turn, and serializes same-session slot reuse. See
        # stackowl.gateway.clarify_pump.ClarifyPump.
        from stackowl.gateway.clarify_pump import ClarifyPump

        cli_pump = ClarifyPump(clarify_gateway, stream_registry, clarify_classifier)

        # §4.3 non-blocking intake — shared by BOTH channel loops. The queued
        # PendingIntake (Task 3) carries only request_id/original_input/target, so
        # to RE-DISPATCH a queued message faithfully (same routing, same clarify
        # interception) the drain needs the original raw IngressMessage. Park it
        # here keyed by request_id; pop it when the queue entry is drained.
        from stackowl.gateway.parked_intakes import ParkedIntakes
        from stackowl.gateway.scanner import IngressMessage, RouteDecision

        # STEER-3/F057 — the park map is now a ParkedIntakes (was a bare dict) so
        # the turn sweep can EVICT entries for reaped wedged/GC'd turns (otherwise
        # a parked raw IngressMessage only pops on a successful drain → a slow
        # leak). Wired to the registry's reaped-evictor hook below.
        _parked_intakes = ParkedIntakes()
        turn_registry.set_reaped_evictor(_parked_intakes.evict)

        async def _dispatch_turn(
            pump: ClarifyPump,
            channel_adapter: _IntakeAdapter,
            msg: IngressMessage,
            decision: RouteDecision,
            input_text: str,
        ) -> None:
            """Build + launch ONE turn: create stream, route, register, spawn send.

            Registers the turn in the shared TurnRegistry (marking the session
            RUNNING) and attaches a completion hook that drains the next queued
            intake for this session (FIFO). Mirrors the legacy inline body but is
            shared by both loops so the intake/queue/drain semantics live in ONE
            place.
            """
            # §4.1 stream re-key: register the response stream by trace_id (the key
            # deliver looks the writer up by), so the turn's output is never
            # stream-missed.
            writer, reader = stream_registry.create(msg.trace_id)
            if decision.route == "parliament" and decision.parliament_owls:
                log.info(
                    "[startup] gateway: routing to parliament",
                    extra={"_fields": {"owls": decision.parliament_owls, "session_id": msg.session_id}},
                )
                producer: asyncio.Task[object] = asyncio.create_task(
                    _deliver_parliament(
                        input_text, decision.parliament_owls, msg.session_id, msg.trace_id,
                    )
                )
            elif decision.route == "command":
                log.info(
                    "[startup] gateway: command route",
                    extra={"_fields": {"cmd": decision.target, "session_id": msg.session_id}},
                )
                cmd_state = PipelineState(
                    trace_id=msg.trace_id,
                    session_id=msg.session_id,
                    input_text=input_text,
                    channel=msg.channel,
                    owl_name="system",
                    pipeline_step="start",
                    interactive=True,  # real user typed a slash command
                    reply_target=msg.chat_id,
                )
                cmd_args = input_text.split(" ", 1)[1] if " " in input_text else ""
                producer = asyncio.create_task(
                    _deliver_command_stub(
                        decision.target, msg.session_id, cmd_state, cmd_args, msg.trace_id,
                    )
                )
            else:
                state = PipelineState(
                    trace_id=msg.trace_id,
                    session_id=msg.session_id,
                    input_text=input_text,
                    channel=msg.channel,
                    owl_name=decision.target,
                    pipeline_step="start",
                    interactive=True,  # real user turn
                    reply_target=msg.chat_id,  # §4.5 — route the reply to ITS chat
                )
                producer = asyncio.create_task(backend.run(state))
            producer.add_done_callback(_log_pipeline_crash)
            # The registry only ever .done()/.cancelled()-inspects the task, so a
            # Task[object] producer (backend.run returns the state; the deliver
            # stubs return None) is safe under the Task[None] slot.
            await turn_registry.register(
                msg.trace_id, session_id=msg.session_id,
                task=cast("asyncio.Task[None]", producer),
                target=msg.chat_id, original_input=input_text,
            )

            # Completion -> FIFO drain seam. When the turn's producer finishes
            # (success, crash, or cancel — all reach here), deregister it and
            # dispatch the next queued intake for this session, so a same-session
            # message that was enqueued mid-turn runs next. Scheduled as a task
            # because the done-callback is sync but drain is async; the task is
            # held in a strong ref so it isn't GC'd mid-flight.
            def _on_done(_prod: asyncio.Task[object], sid: str = msg.session_id,
                         rid: str = msg.trace_id) -> None:
                drain_task = asyncio.create_task(_drain_next(pump, channel_adapter, sid, rid))
                _drain_tasks.add(drain_task)
                drain_task.add_done_callback(_drain_tasks.discard)
                drain_task.add_done_callback(_log_pipeline_crash)

            producer.add_done_callback(_on_done)

            # Decoupled send: frees the loop so a parked clarify turn can receive
            # its answer; the pump closes the writer if the producer crashes so the
            # send can never wedge the session.
            pump.spawn_send(
                channel_adapter=channel_adapter, reader=reader,
                session_id=msg.session_id, request_id=msg.trace_id,
                producer=producer, writer=writer,
            )

        async def _drain_next(
            pump: ClarifyPump,
            channel_adapter: _IntakeAdapter,
            session_id: str,
            finished_request_id: str,
        ) -> None:
            """Deregister the finished turn, then dispatch the next queued intake.

            Fail-safe: any error is logged, never raised (it runs detached in a
            done-callback task). Re-running the dispatch path (scan +
            resolve_or_rewrite) on the parked raw message keeps routing faithful
            and lets a clarify that resolved meanwhile still intercept.
            """
            # §4.3 race fix: hold the per-session intake lock across the WHOLE
            # decide-and-claim sequence (deregister → pop_next → resolve_or_rewrite
            # → _dispatch_turn/register). resolve_or_rewrite AWAITS the LLM
            # classifier when a clarify is pending and YIELDS; during that yield the
            # session is transiently de-registered (looks IDLE). Without the lock a
            # fresh same-session _intake would see IDLE and start a SECOND running
            # turn (two turns for one session → register overwrites the slot,
            # orphaning one + corrupting FIFO/drain). Holding the lock across the
            # await is correct: same-session intake is serialized BY DESIGN (≤1
            # running turn per session); cross-session uses a different lock and is
            # untouched. _dispatch_turn/register do NOT re-acquire this lock (no
            # re-entrancy — acquisition lives only here and in _intake).
            intake_lock = turn_registry.session_intake_lock(session_id)
            # Trace id to recurse-drain on (the consumed-clarify case): None means
            # no same-session recursion. Captured here so the post-lock recurse does
            # NOT dereference the possibly-None `parked` (mypy-narrowing safe).
            recurse_trace_id: str | None = None
            try:
                async with intake_lock:
                    # §9 inv.1 (lost-steer) — the COMPLETION SEAM. The producer
                    # loop has ENDED but the turn is still registered RUNNING. A
                    # steer landing in the window between loop-done and deregister
                    # would otherwise be put onto a mailbox no loop will fold, then
                    # GC'd — a silently lost instruction. finalize_and_drain
                    # ATOMICALLY (under the per-TURN lock) flips RUNNING→FINALIZING
                    # — so a CONCURRENT try_steer now reads FINALIZING and converts
                    # its steer to a queued-new turn — THEN drains any already-
                    # accepted survivor and re-routes each as a queued-new turn
                    # (enqueued onto THIS session, picked up by the pop_next below /
                    # the next drain). Fail-safe: own suppression so a teardown
                    # error never crashes the detached drain. Lock ordering:
                    # finalize_and_drain takes the per-TURN lock (try_steer takes the
                    # SAME, never the session intake lock) — no inversion with the
                    # session intake lock we already hold.
                    try:
                        survivors = await turn_registry.finalize_and_drain(
                            finished_request_id
                        )
                        if survivors:
                            # finalize_and_drain already ENQUEUED each survivor as a
                            # queued-new intake keyed `{rid}-survivor-{i}`. For the
                            # drain's pop_next path below (and subsequent drains) to
                            # re-dispatch them faithfully — same scan + resolve +
                            # dispatch as any queued message — each needs a parked
                            # raw IngressMessage under that SAME key (else the drain
                            # logs "lost its raw message" and DROPS it). Synthesize
                            # one per survivor, inheriting this session/channel and
                            # the finished turn's chat target so the re-routed steer
                            # routes back to its own chat. The trace_id is the
                            # survivor key so the stream/registry keying stays unique.
                            finished_turn = turn_registry.get(finished_request_id)
                            survivor_target = (
                                finished_turn.target if finished_turn is not None else None
                            )
                            for i, text in enumerate(survivors):
                                survivor_rid = f"{finished_request_id}-survivor-{i}"
                                _parked_intakes.put(survivor_rid, IngressMessage(
                                    text=text,
                                    session_id=session_id,
                                    channel=channel_adapter.channel_name,
                                    trace_id=survivor_rid,
                                    chat_id=survivor_target,
                                ))
                            log.info(
                                "[startup] gateway: completion seam drained survivor steers",
                                extra={"_fields": {
                                    "session_id": session_id,
                                    "request_id": finished_request_id,
                                    "survivors": len(survivors),
                                }},
                            )
                    except Exception as exc:  # noqa: BLE001 — backstop: never crash the detached drain
                        # No-silent-catch: the completion-seam teardown is non-fatal
                        # (we still proceed to deregister + pop_next below), but a
                        # swallowed error here must be LOUD, not silent.
                        log.error(
                            "[startup] gateway: finalize_and_drain backstop caught — survivor steers may be lost",
                            exc_info=exc,
                            extra={"_fields": {
                                "session_id": session_id,
                                "request_id": finished_request_id,
                            }},
                        )
                    await turn_registry.deregister(finished_request_id)
                    nxt = turn_registry.pop_next(session_id)
                    if nxt is not None:
                        parked = _parked_intakes.get_and_pop(nxt.request_id)
                        if parked is None:
                            log.error(
                                "[startup] gateway: queued intake lost its raw message — dropping",
                                extra={"_fields": {"request_id": nxt.request_id, "session_id": session_id}},
                            )
                        else:
                            decision = scanner.scan(parked)
                            input_text = (
                                decision.stripped_text if decision.stripped_text is not None else parked.text
                            )
                            consumed, input_text = await pump.resolve_or_rewrite(
                                session_id=parked.session_id, channel=parked.channel,
                                route=decision.route, target=decision.target, input_text=input_text,
                            )
                            if consumed:
                                # The queued message resolved a parked clarify in
                                # place — no new turn; the session is now idle.
                                # Recurse OUTSIDE this locked block (after release)
                                # to drain the NEXT one without self-deadlocking on
                                # the same per-session lock.
                                recurse_trace_id = parked.trace_id
                            else:
                                await _dispatch_turn(pump, channel_adapter, parked, decision, input_text)
                if recurse_trace_id is not None:
                    await _drain_next(pump, channel_adapter, session_id, recurse_trace_id)
            except Exception as exc:  # noqa: BLE001 — detached drain guard
                log.error(
                    "[startup] gateway: drain-next failed — session may stall",
                    exc_info=exc,
                    extra={"_fields": {"session_id": session_id}},
                )
            # §4.7 global-cap WAKE: this turn's completion freed a global slot. A
            # turn HELD earlier because the host was at the global cap was enqueued
            # on its OWN (idle) session, so its per-session completion hook never
            # fires (that session has no running turn to complete). Surface one such
            # stranded session and dispatch its head intake. Runs AFTER releasing
            # THIS session's lock (the held turn lives on a DIFFERENT session with
            # its own lock — never nest the two). Fail-safe: own try/except so a
            # wake error never stalls the seam.
            try:
                await _wake_global_held(pump, channel_adapter)
            except Exception as exc:  # noqa: BLE001 — backstop: never stall the seam
                # No-silent-catch: a wake error is non-fatal (this turn's seam is
                # done; the held turn can be woken by a later completion), but it
                # must be LOUD, not silent.
                log.error(
                    "[startup] gateway: _wake_global_held backstop caught — a globally-held turn may stay parked",
                    exc_info=exc,
                    extra={"_fields": {"session_id": session_id}},
                )

        async def _wake_global_held(
            pump: ClarifyPump,
            channel_adapter: _IntakeAdapter,
        ) -> None:
            """Dispatch one globally-held turn now that a global slot has freed.

            Finds an idle session with a queued intake (the global-cap hold shape),
            then under THAT session's intake lock re-checks capacity and dispatches
            its head intake faithfully (same scan + resolve_or_rewrite + dispatch as
            the per-session drain). Recurses to wake the next holder while capacity
            remains. Fail-safe per the caller's suppression; bounded by the finite
            number of queued holders.
            """
            sid = turn_registry.idle_queued_session()
            if sid is None:
                return
            wake_lock = turn_registry.session_intake_lock(sid)
            woke = False
            async with wake_lock:
                # Re-check under the lock: capacity may have re-saturated and the
                # session may have started running between find and acquire.
                if (
                    turn_registry.running(sid) is not None
                    or turn_registry.at_global_capacity()
                ):
                    return
                nxt = turn_registry.pop_next(sid)
                if nxt is None:
                    return
                parked = _parked_intakes.get_and_pop(nxt.request_id)
                if parked is None:
                    log.error(
                        "[startup] gateway: held intake lost its raw message — dropping",
                        extra={"_fields": {"request_id": nxt.request_id, "session_id": sid}},
                    )
                    return
                decision = scanner.scan(parked)
                input_text = (
                    decision.stripped_text if decision.stripped_text is not None else parked.text
                )
                consumed, input_text = await pump.resolve_or_rewrite(
                    session_id=parked.session_id, channel=parked.channel,
                    route=decision.route, target=decision.target, input_text=input_text,
                )
                if not consumed:
                    await _dispatch_turn(pump, channel_adapter, parked, decision, input_text)
                    woke = True
            # Only recurse on REAL progress (we dispatched a holder): there may be
            # MORE holders while slots remain, so wake the next OUTSIDE this lock
            # (the next holder has a different session lock). The re-check at the top
            # stops the recursion once capacity re-saturates or no holder remains —
            # gating on `woke` guarantees termination (no progress -> no recurse).
            if woke:
                with contextlib.suppress(Exception):
                    await _wake_global_held(pump, channel_adapter)

        # F050 — wire the turn-sweeper's stranded-session drain to the SAME global-
        # cap wake seam. When ``sweep`` reaps a wedged turn (done but never DONE) and
        # frees its ``_running`` slot, that session may hold a queued intake with no
        # running turn to wake it (silent unresponsiveness). ``_wake_global_held``
        # surfaces exactly that ``idle_queued_session`` shape and dispatches its head
        # intake faithfully — so a reap becomes a real re-dispatch, not fake success.
        # Bound to the CLI pump/adapter (the always-present local loop); both
        # ``sweep`` and ``_wake_global_held`` self-suppress so a wake error is loud
        # but never crashes the scheduler loop.
        async def _drain_stranded_after_reap() -> None:
            await _wake_global_held(cli_pump, adapter)

        turn_registry.set_stranded_drainer(_drain_stranded_after_reap)

        async def _intake(
            pump: ClarifyPump,
            channel_adapter: _IntakeAdapter,
            msg: IngressMessage,
            decision: RouteDecision,
            input_text: str,
        ) -> None:
            """Non-blocking intake: dispatch if idle, ROUTE if a same-session turn
            runs (P3), else hold under the global cap.

            Replaces the blocking ``serialize_prior`` gate (§4.3). Within a chat at
            most one RUNNING turn. §6/§7 (P3 Task 16): a message that arrives while a
            same-session turn is in flight is ROUTED — STEER folds it into the
            running turn's mailbox, STOP cooperatively halts it, NEW becomes a
            queued-new turn (dispatched on completion via the _drain_next hook).
            """
            # §4.3 race fix: hold the per-session intake lock across the
            # running()-check → (dispatch+register) | (enqueue) DECISION. This makes
            # the "decide dispatch-vs-enqueue and claim the running slot" section
            # mutually exclusive with the detached _drain_next (which holds the SAME
            # lock across its resolve_or_rewrite await). So a fresh same-session
            # message that arrives while drain is mid-decision BLOCKS here until
            # drain has re-registered, then correctly sees the session RUNNING.
            # _dispatch_turn/register do NOT re-acquire this lock (no re-entrancy).
            #
            # §4.7 cap enforcement (Task 8): the dispatch branch ALSO gates on the
            # host global cap, and EVERY enqueue is guarded against QueueFull —
            # both INSIDE this lock (check-then-act stays atomic vs _drain_next).
            #
            # §6/§7 lock discipline (P3 Task 16): the TurnRouter.route() call is a
            # SLOW LLM hop (is_steer + optional veto). We MUST NOT hold the intake
            # lock across it — that would block this session's completion→drain
            # seam. So when a same-session turn is running we CAPTURE it, RELEASE the
            # lock, then route OUTSIDE the lock. STEER (try_steer) is atomic under
            # the per-TURN lock and converts to queued-new if the turn finished
            # mid-route; STOP is a flag-set; only NEW re-acquires THIS lock to
            # enqueue under a fresh running()-recheck.
            #   ack_kind == "queued"  -> routed NEW: queued-new turn (FIFO)
            #   ack_kind == "steered" -> routed STEER/STOP: folded/halted, no enqueue
            #   ack_kind == "busy"    -> held because the host is at the global cap
            #   ack_kind == "overflow"-> per-session queue full: notice + DROP
            ack_kind = ""
            running_turn = None
            routed_signal = None  # the router's STEER/STOP/NEW verdict (if routed)
            async with turn_registry.session_intake_lock(msg.session_id):
                running_turn = turn_registry.running(msg.session_id)
                session_idle = running_turn is None
                # Idle + capacity → dispatch a fresh turn now (unchanged §4.3/§4.7).
                if session_idle and not turn_registry.at_global_capacity():
                    await _dispatch_turn(pump, channel_adapter, msg, decision, input_text)
                elif not session_idle:
                    # A same-session turn is RUNNING → defer to the TurnRouter, but
                    # OUTSIDE the lock (it is a slow LLM hop). We do NOT enqueue here;
                    # the post-lock routing decides STEER/STOP/NEW. (running_turn is
                    # captured above for the route.)
                    pass
                else:
                    # session_idle but at the GLOBAL cap → hold this fresh-session
                    # turn (bounded enqueue) so it runs when capacity frees. No
                    # routing: there is no same-session running turn to steer/stop.
                    # Park the raw message so the global-cap WAKE seam can re-dispatch.
                    _parked_intakes.put(msg.trace_id, msg)
                    try:
                        turn_registry.enqueue(
                            msg.session_id, original_input=input_text,
                            request_id=msg.trace_id, target=msg.chat_id,
                        )
                    except QueueFull as exc:
                        # Loud overflow: never silently grow, never crash the loop.
                        _parked_intakes.get_and_pop(msg.trace_id)
                        log.warning(
                            "[startup] gateway: intake queue full — dropping with notice",
                            extra={"_fields": {
                                "session_id": msg.session_id,
                                "request_id": msg.trace_id,
                                "reason": str(exc),
                            }},
                        )
                        ack_kind = "overflow"
                    else:
                        # A globally-held turn sits on an idle session (no running
                        # turn to fire its completion->drain hook), so the busy ack
                        # doubles as the global-cap-WAKE signal for _drain_next.
                        ack_kind = "busy"
                        log.info(
                            "[startup] gateway: turn held — global cap (busy)",
                            extra={"_fields": {
                                "session_id": msg.session_id,
                                "request_id": msg.trace_id,
                                "hold_reason": ack_kind,
                            }},
                        )

            # §6/§7 (P3 Task 16) — ROUTE the mid-turn message OUTSIDE the intake lock
            # (released above): route() is a slow LLM hop and must not block the
            # session's completion→drain seam. STEER/STOP are acted on by the helper
            # (atomic under the per-TURN lock / a flag-set); NEW returns ENQUEUE_NEW,
            # which RE-ACQUIRES this intake lock briefly + RE-CHECKS running() (it may
            # have finished during the slow route → dispatch now) before enqueueing.
            if running_turn is not None:
                from stackowl.gateway.inflight_router import (
                    InflightAction,
                    route_inflight_message,
                )

                # is_reply_to_inflight: a STRUCTURAL reply-to-the-running-message
                # signal (STEER-1/F060). The channel (Telegram) stamps
                # ``msg.is_reply`` when the inbound message replies to one of the
                # bot's own messages; it becomes a structural STEER only when a turn
                # is actually in-flight (``running_turn is not None`` — already true
                # on this branch). ``resolve_reply_to_inflight`` makes that contract
                # explicit + unit-testable (a reply to an OLD bot message with
                # nothing running is a normal message, never a spurious steer).
                outcome = await route_inflight_message(
                    router=turn_router,
                    registry=turn_registry,
                    running=running_turn,
                    text=input_text,
                    session_id=msg.session_id,
                    request_id_new=msg.trace_id,
                    target=msg.chat_id,
                    is_reply_to_inflight=resolve_reply_to_inflight(
                        is_reply=msg.is_reply, turn_running=True
                    ),
                )
                routed_signal = outcome.signal
                if outcome.action is InflightAction.HANDLED:
                    # STEER folded into the running turn's mailbox (or converted to
                    # queued-new by try_steer if the turn finished mid-route), or
                    # STOP set the cooperative-stop flag. Nothing to enqueue here.
                    ack_kind = "steered"
                else:
                    # NEW → queued-new. The routed body has any explicit-signal token
                    # (/new) ALREADY STRIPPED, so we must re-route the BODY as a fresh
                    # turn — NOT the original "/new …" command scan (which would
                    # misroute as a slash command). Synthesize a raw IngressMessage
                    # carrying the stripped body and RE-SCAN it; this is also what the
                    # _drain_next pop path re-scans, so park THIS synthesized message
                    # (its text is the body) — never the raw "/new …" message.
                    routed_msg = IngressMessage(
                        text=outcome.routed_text,
                        session_id=msg.session_id,
                        channel=msg.channel,
                        trace_id=msg.trace_id,
                        chat_id=msg.chat_id,
                    )
                    routed_decision = scanner.scan(routed_msg)
                    routed_input = (
                        routed_decision.stripped_text
                        if routed_decision.stripped_text is not None
                        else routed_msg.text
                    )
                    # Re-acquire the intake lock + RE-CHECK running() (the turn may
                    # have finished during the slow route → dispatch immediately
                    # instead of enqueueing behind nothing).
                    async with turn_registry.session_intake_lock(msg.session_id):
                        if (
                            turn_registry.running(msg.session_id) is None
                            and not turn_registry.at_global_capacity()
                        ):
                            await _dispatch_turn(
                                pump, channel_adapter, routed_msg, routed_decision, routed_input
                            )
                            ack_kind = "dispatched"
                        else:
                            _parked_intakes.put(msg.trace_id, routed_msg)
                            try:
                                turn_registry.enqueue(
                                    msg.session_id, original_input=routed_input,
                                    request_id=msg.trace_id, target=msg.chat_id,
                                )
                            except QueueFull as exc:
                                _parked_intakes.get_and_pop(msg.trace_id)
                                log.warning(
                                    "[startup] gateway: intake queue full — dropping with notice",
                                    extra={"_fields": {
                                        "session_id": msg.session_id,
                                        "request_id": msg.trace_id,
                                        "reason": str(exc),
                                    }},
                                )
                                ack_kind = "overflow"
                            else:
                                ack_kind = "queued"
                                log.info(
                                    "[startup] gateway: routed NEW — queued intake",
                                    extra={"_fields": {
                                        "session_id": msg.session_id,
                                        "request_id": msg.trace_id,
                                    }},
                                )

            # Ack/notice OUTSIDE the lock — the network send must not hold the
            # intake critical section (and a slow send must never block drain/intake).
            if ack_kind == "overflow":
                with contextlib.suppress(Exception):
                    await channel_adapter.send_text("Too many queued messages — please wait.")
            elif ack_kind == "busy":
                with contextlib.suppress(Exception):
                    await channel_adapter.send_text("Busy — I'll start that as soon as I have capacity.")
            elif ack_kind == "queued":
                with contextlib.suppress(Exception):
                    await channel_adapter.send_text("Queued — I'll start that next.")
            elif ack_kind == "steered" and routed_signal is ExplicitSignal.STOP:
                # A STEER folds silently into the running turn (the user sees the
                # turn's own evolving output); a STOP gets an explicit acknowledgement.
                with contextlib.suppress(Exception):
                    await channel_adapter.send_text(
                        "Stopping the current task at the next safe point."
                    )

        async def _message_loop() -> None:
            log.info("[startup] gateway: message loop started")
            try:
                while True:
                    try:
                        msg = await adapter.receive()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 — top-level loop guard
                        log.error(
                            "[startup] gateway: receive failed — sleeping then retrying",
                            exc_info=exc,
                        )
                        await asyncio.sleep(1.0)
                        continue
                    try:
                        log.info(
                            "[startup] gateway: message received",
                            extra={"_fields": {"session_id": msg.session_id, "text_len": len(msg.text)}},
                        )
                        decision = scanner.scan(msg)
                        input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
                        # E5 — a reply to a pending clarify resumes its turn (or,
                        # in turn-yield fallback, seeds a fresh resume turn).
                        consumed, input_text = await cli_pump.resolve_or_rewrite(
                            session_id=msg.session_id, channel=msg.channel,
                            route=decision.route, target=decision.target, input_text=input_text,
                        )
                        if consumed:
                            continue
                        # §4.3 non-blocking intake: dispatch if the session is idle,
                        # else enqueue FIFO + instant-ack (no blocking on the running
                        # turn — serialize_prior is GONE). The running turn's
                        # completion drains the next queued intake.
                        await _intake(cli_pump, adapter, msg, decision, input_text)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 — top-level loop guard
                        log.error(
                            "[startup] gateway: message processing failed — continuing",
                            exc_info=exc,
                            extra={"_fields": {"session_id": getattr(msg, "session_id", "?")}},
                        )
                        # Clean up the stream so it doesn't leak. §4.1: keyed by
                        # trace_id (the stream key), matching create/spawn_send.
                        with contextlib.suppress(Exception):
                            stream_registry.remove(getattr(msg, "trace_id", ""))
                        continue
            except asyncio.CancelledError:
                log.info("[startup] gateway: message loop cancelled")
                raise

        # 3. STEP — start Telegram adapter if configured
        from stackowl.config.secret_resolver import SecretResolver

        telegram_adapter = None
        telegram_loop_task = None
        tg_cfg = self._settings.telegram_channel
        if tg_cfg.bot_token:
            log.info("[startup] gateway: starting Telegram adapter")
            try:
                resolved_token = SecretResolver.resolve(tg_cfg.bot_token)
                resolved_webhook_secret = (
                    SecretResolver.resolve(tg_cfg.webhook_secret) if tg_cfg.webhook_secret else ""
                )
            except ConfigurationError as exc:
                log.error(
                    "[startup] gateway: Telegram secret resolution failed — skipping",
                    exc_info=exc,
                )
            else:
                from stackowl.channels.telegram.adapter import TelegramChannelAdapter

                resolved_tg_settings = tg_cfg.model_copy(
                    update={"bot_token": resolved_token, "webhook_secret": resolved_webhook_secret}
                )
                telegram_adapter = TelegramChannelAdapter(resolved_tg_settings)

                # E0-S1 — wire the Telegram consent round-trip BEFORE start() so a
                # message arriving at boot can never miss its prompter (would else
                # fail closed with a spurious denial). The prompter only needs the
                # adapter object; the callback handler is attached after start()
                # (it needs the live bot application).
                from stackowl.channels.telegram.consent import TelegramConsentPrompter

                tg_consent_prompter = TelegramConsentPrompter(telegram_adapter)
                consent_routing.register("telegram", tg_consent_prompter)

                await telegram_adapter.start()
                # E5 — let the clarify gateway deliver questions over Telegram,
                # and give the Telegram loop its own clarify-aware dispatch pump.
                clarify_gateway.register_adapter("telegram", telegram_adapter)
                tg_pump = ClarifyPump(clarify_gateway, stream_registry, clarify_classifier)

                try:
                    from stackowl.channels.telegram.callbacks import CallbackRouter

                    tg_callback_router = CallbackRouter(db_pool, telegram_adapter)
                    await tg_callback_router.ensure_table()
                    tg_callback_router.register("consent:", tg_consent_prompter.handle_callback)
                    # E5-C — a tapped clarify choice button resolves the parked
                    # turn (parallel to the typed-reply path handled by the loop).
                    # NOTE: consent and clarify share this router but resolve very
                    # differently — consent awaits a local Future in-coroutine;
                    # clarify resolves a gateway Event across the decoupled pump's
                    # turn-yield/blocking duality. Do NOT refactor them into a
                    # shared base — a local Future would deadlock clarify's
                    # resolve-before-park case.
                    from stackowl.channels.telegram.clarify import TelegramClarifyResolver

                    tg_clarify_resolver = TelegramClarifyResolver(clarify_gateway)
                    tg_callback_router.register("clarify:", tg_clarify_resolver.handle_callback)
                    telegram_adapter.attach_callback_router(tg_callback_router)
                    log.info("[startup] gateway: Telegram consent + clarify callbacks wired")
                except Exception as exc:
                    log.error(
                        "[startup] gateway: Telegram consent callback wiring failed — "
                        "consequential actions on Telegram will fail closed",
                        exc_info=exc,
                    )

                async def _telegram_loop() -> None:
                    log.info("[startup] gateway: telegram loop started")
                    try:
                        while True:
                            try:
                                msg = await telegram_adapter.receive()
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:  # noqa: BLE001 — top-level loop guard
                                log.error(
                                    "[startup] gateway: telegram receive failed — sleeping then retrying",
                                    exc_info=exc,
                                )
                                await asyncio.sleep(2.0)
                                continue
                            try:
                                log.info(
                                    "[startup] gateway: telegram message received",
                                    extra={"_fields": {"session_id": msg.session_id, "text_len": len(msg.text)}},
                                )
                                decision = scanner.scan(msg)
                                input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
                                # E5 — resolve a reply into its parked clarify turn.
                                consumed, input_text = await tg_pump.resolve_or_rewrite(
                                    session_id=msg.session_id, channel=msg.channel,
                                    route=decision.route, target=decision.target, input_text=input_text,
                                )
                                if consumed:
                                    continue
                                # §4.3 non-blocking intake (identical to the CLI
                                # loop): dispatch if idle, else enqueue FIFO +
                                # instant-ack. serialize_prior is GONE; the running
                                # turn's completion drains the next queued intake.
                                await _intake(tg_pump, telegram_adapter, msg, decision, input_text)
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:  # noqa: BLE001 — top-level loop guard
                                log.error(
                                    "[startup] gateway: telegram message processing failed — continuing",
                                    exc_info=exc,
                                    extra={"_fields": {"session_id": getattr(msg, "session_id", "?")}},
                                )
                                # §4.1: keyed by trace_id (the stream key).
                                with contextlib.suppress(Exception):
                                    stream_registry.remove(getattr(msg, "trace_id", ""))
                                continue
                    except asyncio.CancelledError:
                        log.info("[startup] gateway: telegram loop cancelled")
                        raise

                telegram_loop_task = asyncio.create_task(_telegram_loop())
                log.info("[startup] gateway: Telegram adapter started")
        else:
            log.info("[startup] gateway: no Telegram bot_token — skipping")

        # 3b. STEP — start the Slack adapter if configured (mirrors the Telegram
        # block). Socket Mode needs BOTH the bot token (xoxb-) and the app-level
        # token (xapp-, connections:write); without both we skip. The Bolt app +
        # socket handler are constructed INSIDE this block (so slack_bolt is only
        # imported when Slack is actually configured), the socket connection runs
        # as a BACKGROUND task (never blocks boot), and the inbound events route
        # through the SHARED gateway machinery via `_slack_loop` (byte-for-byte
        # the Telegram loop with a Slack pump/adapter).
        slack_adapter = None
        slack_loop_task = None
        slack_socket_task = None
        slack_socket_handler = None
        slack_cfg = self._settings.slack_channel
        if slack_cfg.bot_token and slack_cfg.app_token:
            log.info("[startup] gateway: starting Slack adapter")
            try:
                resolved_bot_token = SecretResolver.resolve(slack_cfg.bot_token)
                resolved_app_token = SecretResolver.resolve(slack_cfg.app_token)
                resolved_signing_secret = (
                    SecretResolver.resolve(slack_cfg.signing_secret)
                    if slack_cfg.signing_secret
                    else ""
                )
            except ConfigurationError as exc:
                log.error(
                    "[startup] gateway: Slack secret resolution failed — skipping",
                    exc_info=exc,
                )
            else:
                from stackowl.channels.slack.adapter import SlackChannelAdapter

                resolved_slack_settings = slack_cfg.model_copy(
                    update={
                        "bot_token": resolved_bot_token,
                        "app_token": resolved_app_token,
                        "signing_secret": resolved_signing_secret,
                    }
                )
                slack_adapter = SlackChannelAdapter(resolved_slack_settings)

                # Build the live Bolt app + wire it to the adapter. The imports
                # live INSIDE this block so slack_bolt is only required when Slack
                # is configured (parallel to PTB inside the Telegram block).
                from slack_bolt.adapter.socket_mode.async_handler import (
                    AsyncSocketModeHandler,
                )
                from slack_bolt.async_app import AsyncApp

                from stackowl.channels.slack.slash_bridge import (
                    SlackSlashCommandBridge,
                )

                app = AsyncApp(
                    token=resolved_bot_token, signing_secret=resolved_signing_secret
                )
                slack_adapter.set_bolt_app(app)

                # Slack B3 — wire the consent round-trip BEFORE the socket starts
                # (mirrors Telegram ~register-before-start): a consent request
                # arriving at boot must never miss its prompter and spuriously
                # deny. The prompter only needs the adapter object; the @app.action
                # seam that FEEDS its handle_action is registered just below (Bolt
                # registers handlers on the app before the socket connects).
                from stackowl.channels.slack.consent import SlackConsentPrompter

                slack_consent_prompter = SlackConsentPrompter(slack_adapter)
                consent_routing.register("slack", slack_consent_prompter)

                # Resolve the bot's own user id so self-mentions are stripped.
                # Skip-on-failure (log loudly) — an auth_test hiccup must NOT
                # wedge boot; mention-stripping degrades, the channel still runs.
                try:
                    auth = await app.client.auth_test()
                    slack_adapter.set_bot_user_id(str(auth["user_id"]))
                    log.info("[startup] gateway: Slack auth_test resolved bot user id")
                except Exception as exc:
                    log.error(
                        "[startup] gateway: Slack auth_test failed — bot mentions "
                        "may not be stripped; continuing",
                        exc_info=exc,
                    )

                # Inbound event handlers: route Slack events → the adapter, which
                # filters (allowlist), strips the self-mention, and enqueues an
                # IngressMessage for `_slack_loop` to pump through the gateway.
                slack_slash_bridge = SlackSlashCommandBridge()

                @app.event("message")
                async def _slack_on_message(event: dict[str, object], say: object) -> None:
                    # A bot's own posts echo back as message events — ignore them
                    # (no user → not a real inbound turn) to avoid a self-loop.
                    if event.get("bot_id") is not None or event.get("subtype") is not None:
                        return
                    user_id = str(event.get("user", ""))
                    text = str(event.get("text", ""))
                    if not user_id:
                        return
                    await slack_adapter.handle_event(event, user_id, text)

                @app.event("app_mention")
                async def _slack_on_app_mention(event: dict[str, object], say: object) -> None:
                    user_id = str(event.get("user", ""))
                    text = str(event.get("text", ""))
                    if not user_id:
                        return
                    await slack_adapter.handle_event(event, user_id, text)

                @app.command(re.compile(r".*"))
                async def _slack_on_command(ack: object, command: dict[str, object], respond: object) -> None:
                    # Bolt requires the slash command be acked within 3s.
                    with contextlib.suppress(Exception):
                        await ack()  # type: ignore[operator]
                    name = str(command.get("command", ""))
                    text = str(command.get("text", ""))
                    user_id = str(command.get("user_id", ""))
                    try:
                        reply = await slack_slash_bridge.handle_slash_command(
                            name, text, user_id
                        )
                        await respond(reply)  # type: ignore[operator]
                    except Exception as exc:  # noqa: BLE001 — handler guard
                        log.error(
                            "[startup] gateway: Slack slash command failed",
                            exc_info=exc,
                            extra={"_fields": {"command": name}},
                        )

                # Slack B3 — the inbound INTERACTIVITY seam: a tapped Block Kit
                # button (consent / clarify / memory) arrives as a Bolt
                # block_actions event. Build the prefix router + per-prefix
                # handlers, then register a catch-all @app.action that acks FIRST
                # (Bolt's 3s deadline) and routes the tap. Registered BEFORE the
                # socket starts (handlers attach on the app pre-connect), so the
                # very first tap is routed. Mirrors the Telegram callback wiring.
                try:
                    from stackowl.channels.slack.callbacks import SlackActionRouter
                    from stackowl.channels.slack.clarify import SlackClarifyResolver
                    from stackowl.channels.slack.memory_callbacks import (
                        SlackMemoryActionHandler,
                    )

                    slack_router = SlackActionRouter()
                    slack_router.register(
                        "consent:", slack_consent_prompter.handle_action
                    )
                    # NOTE: consent and clarify share this router but resolve very
                    # differently — consent awaits a local Future, clarify resolves
                    # a gateway Event across the decoupled pump (mirrors the
                    # Telegram do-not-unify note).
                    slack_clarify_resolver = SlackClarifyResolver(clarify_gateway)
                    slack_router.register(
                        "clarify:", slack_clarify_resolver.handle_action
                    )
                    # The memory approve/reject taps need a bridge exposing BOTH
                    # force_promote (FactPromoter) AND delete (the bridge); neither
                    # alone has both, so hand the handler a composite that
                    # delegates each op to its owner.
                    slack_memory_bridge = _SlackMemoryBridgeComposite(
                        bridge=memory_bridge,
                        promoter=memory_components.promoter,
                    )
                    slack_memory_handler = SlackMemoryActionHandler(
                        slack_memory_bridge  # type: ignore[arg-type]
                    )
                    slack_memory_handler.register(slack_router)

                    @app.action(re.compile(r".*"))
                    async def _slack_on_action(
                        ack: object, body: dict[str, object]
                    ) -> None:
                        # Ack within Bolt's 3s deadline FIRST — a slow/raising
                        # route must never miss the ack and wedge the socket.
                        with contextlib.suppress(Exception):
                            await ack()  # type: ignore[operator]
                        try:
                            actions = body.get("actions") or []
                            action = actions[0] if isinstance(actions, list) and actions else {}
                            action_id = str(
                                action.get("action_id") or action.get("value") or ""
                            )
                            # A unique-per-delivery id for at-least-once de-dup
                            # (Bolt re-delivers on hiccups): the action's ts, else
                            # the interaction trigger id, else the action id.
                            delivery_id = str(
                                action.get("action_ts")
                                or body.get("trigger_id")
                                or action_id
                            )
                            await slack_router.route(action_id, delivery_id=delivery_id)
                        except Exception as exc:  # noqa: BLE001 — handler guard
                            log.error(
                                "[startup] gateway: Slack action routing failed",
                                exc_info=exc,
                            )

                    log.info(
                        "[startup] gateway: Slack consent + clarify + memory "
                        "interactivity wired"
                    )
                except Exception as exc:
                    log.error(
                        "[startup] gateway: Slack interactivity wiring failed — "
                        "consequential actions on Slack will fail closed",
                        exc_info=exc,
                    )

                slack_adapter.register_with_registry()
                # Let the clarify gateway deliver questions over Slack, and give
                # the Slack loop its own clarify-aware dispatch pump.
                clarify_gateway.register_adapter("slack", slack_adapter)
                slack_pump = ClarifyPump(clarify_gateway, stream_registry, clarify_classifier)

                # Open the Socket Mode connection as a BACKGROUND task — boot must
                # never block on the live WebSocket handshake.
                slack_socket_handler = AsyncSocketModeHandler(app, resolved_app_token)
                slack_socket_task = asyncio.create_task(
                    slack_socket_handler.start_async()  # type: ignore[no-untyped-call]
                )

                async def _slack_loop() -> None:
                    log.info("[startup] gateway: slack loop started")
                    try:
                        while True:
                            try:
                                msg = await slack_adapter.receive()
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:  # noqa: BLE001 — top-level loop guard
                                log.error(
                                    "[startup] gateway: slack receive failed — sleeping then retrying",
                                    exc_info=exc,
                                )
                                await asyncio.sleep(2.0)
                                continue
                            try:
                                log.info(
                                    "[startup] gateway: slack message received",
                                    extra={"_fields": {"session_id": msg.session_id, "text_len": len(msg.text)}},
                                )
                                decision = scanner.scan(msg)
                                input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
                                # E5 — resolve a reply into its parked clarify turn.
                                consumed, input_text = await slack_pump.resolve_or_rewrite(
                                    session_id=msg.session_id, channel=msg.channel,
                                    route=decision.route, target=decision.target, input_text=input_text,
                                )
                                if consumed:
                                    continue
                                # §4.3 non-blocking intake (identical to the CLI/
                                # Telegram loops): dispatch if idle, else enqueue
                                # FIFO + instant-ack; the running turn's completion
                                # drains the next queued intake.
                                await _intake(slack_pump, slack_adapter, msg, decision, input_text)
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:  # noqa: BLE001 — top-level loop guard
                                log.error(
                                    "[startup] gateway: slack message processing failed — continuing",
                                    exc_info=exc,
                                    extra={"_fields": {"session_id": getattr(msg, "session_id", "?")}},
                                )
                                # §4.1: keyed by trace_id (the stream key).
                                with contextlib.suppress(Exception):
                                    stream_registry.remove(getattr(msg, "trace_id", ""))
                                continue
                    except asyncio.CancelledError:
                        log.info("[startup] gateway: slack loop cancelled")
                        raise

                slack_loop_task = asyncio.create_task(_slack_loop())
                log.info("[startup] gateway: Slack adapter started")
        else:
            log.info("[startup] gateway: no Slack bot_token/app_token — skipping")

        # 3c. STEP — start the Discord adapter if configured + ENABLED (F004-part2,
        # mirrors the Telegram block). Gated on BOTH a bot_token AND the
        # ``enabled`` flag (default False): the channel is never started before its
        # send path + consent prompter are wired. Consent/clarify/memory are wired
        # BEFORE start() so a message arriving at boot never misses its prompter
        # (else it fails closed with a spurious denial). The inbound loop is
        # byte-for-byte the Telegram loop with a Discord pump/adapter.
        discord_adapter = None
        discord_loop_task = None
        discord_cfg = self._settings.discord_channel
        if discord_cfg.enabled and discord_cfg.bot_token:
            log.info("[startup] gateway: starting Discord adapter")
            try:
                resolved_discord_token = SecretResolver.resolve(discord_cfg.bot_token)
            except ConfigurationError as exc:
                log.error(
                    "[startup] gateway: Discord secret resolution failed — skipping",
                    exc_info=exc,
                )
            else:
                from stackowl.channels.discord.adapter import DiscordChannelAdapter
                from stackowl.channels.discord.callbacks import DiscordCallbackRouter
                from stackowl.channels.discord.clarify import DiscordClarifyResolver
                from stackowl.channels.discord.consent import DiscordConsentPrompter
                from stackowl.channels.discord.memory_callbacks import (
                    DiscordMemoryCallbackHandler,
                )

                resolved_discord_settings = discord_cfg.model_copy(
                    update={"bot_token": resolved_discord_token}
                )
                discord_adapter = DiscordChannelAdapter(resolved_discord_settings)

                # Wire the consent round-trip BEFORE start() (mirrors Telegram).
                discord_consent_prompter = DiscordConsentPrompter(discord_adapter)
                consent_routing.register("discord", discord_consent_prompter)

                # Build the prefix router + per-prefix handlers, then attach it so
                # the View buttons (consent/clarify/memory) route their custom_id.
                try:
                    discord_router = DiscordCallbackRouter()
                    discord_router.register(
                        "consent:", discord_consent_prompter.handle_callback
                    )
                    discord_clarify_resolver = DiscordClarifyResolver(clarify_gateway)
                    discord_router.register(
                        "clarify:", discord_clarify_resolver.handle_callback
                    )
                    # Memory taps need a bridge exposing BOTH force_promote AND
                    # delete; reuse the Slack composite (same requirement).
                    discord_memory_bridge = _SlackMemoryBridgeComposite(
                        bridge=memory_bridge,
                        promoter=memory_components.promoter,
                    )
                    discord_memory_handler = DiscordMemoryCallbackHandler(
                        discord_memory_bridge  # type: ignore[arg-type]
                    )
                    discord_memory_handler.register(discord_router)
                    discord_adapter.attach_callback_router(discord_router)
                    log.info(
                        "[startup] gateway: Discord consent + clarify + memory wired"
                    )
                except Exception as exc:
                    log.error(
                        "[startup] gateway: Discord interactivity wiring failed — "
                        "consequential actions on Discord will fail closed",
                        exc_info=exc,
                    )

                await discord_adapter.start()
                clarify_gateway.register_adapter("discord", discord_adapter)
                discord_pump = ClarifyPump(
                    clarify_gateway, stream_registry, clarify_classifier
                )

                async def _discord_loop() -> None:
                    log.info("[startup] gateway: discord loop started")
                    try:
                        while True:
                            try:
                                msg = await discord_adapter.receive()
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:  # noqa: BLE001 — top-level loop guard
                                log.error(
                                    "[startup] gateway: discord receive failed — sleeping then retrying",
                                    exc_info=exc,
                                )
                                await asyncio.sleep(2.0)
                                continue
                            try:
                                log.info(
                                    "[startup] gateway: discord message received",
                                    extra={"_fields": {"session_id": msg.session_id, "text_len": len(msg.text)}},
                                )
                                decision = scanner.scan(msg)
                                input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
                                consumed, input_text = await discord_pump.resolve_or_rewrite(
                                    session_id=msg.session_id, channel=msg.channel,
                                    route=decision.route, target=decision.target, input_text=input_text,
                                )
                                if consumed:
                                    continue
                                await _intake(discord_pump, discord_adapter, msg, decision, input_text)
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:  # noqa: BLE001 — top-level loop guard
                                log.error(
                                    "[startup] gateway: discord message processing failed — continuing",
                                    exc_info=exc,
                                    extra={"_fields": {"session_id": getattr(msg, "session_id", "?")}},
                                )
                                with contextlib.suppress(Exception):
                                    stream_registry.remove(getattr(msg, "trace_id", ""))
                                continue
                    except asyncio.CancelledError:
                        log.info("[startup] gateway: discord loop cancelled")
                        raise

                discord_loop_task = asyncio.create_task(_discord_loop())
                log.info("[startup] gateway: Discord adapter started")
        else:
            log.info(
                "[startup] gateway: Discord not enabled or no bot_token — skipping"
            )

        # 3d. STEP — start the WhatsApp adapter if ENABLED (F004-part2, mirrors the
        # Telegram block). WhatsApp Web is QR-auth (no bot token), so the gate is
        # the ``enabled`` flag alone (default False). Consent is wired BEFORE
        # start(); clarify is INHERITED (base numbered text — no buttons). The
        # WhatsApp loop additionally checks the consent prompter's resolve_reply
        # FIRST (a numbered consent reply resolves the parked Future), then the
        # clarify pump, then intake — so a "reply N" consent answer is consumed
        # before it is mistaken for a new turn.
        whatsapp_adapter = None
        whatsapp_loop_task = None
        whatsapp_cfg = self._settings.whatsapp_channel
        if whatsapp_cfg.enabled:
            log.info("[startup] gateway: starting WhatsApp adapter")
            try:
                from stackowl.channels.whatsapp.adapter import WhatsAppChannelAdapter
                from stackowl.channels.whatsapp.consent import WhatsAppConsentPrompter

                whatsapp_adapter = WhatsAppChannelAdapter(whatsapp_cfg)

                # Wire the consent round-trip BEFORE start() (mirrors Telegram).
                whatsapp_consent_prompter = WhatsAppConsentPrompter(whatsapp_adapter)
                consent_routing.register("whatsapp", whatsapp_consent_prompter)

                await whatsapp_adapter.start()
                clarify_gateway.register_adapter("whatsapp", whatsapp_adapter)
                whatsapp_pump = ClarifyPump(
                    clarify_gateway, stream_registry, clarify_classifier
                )

                async def _whatsapp_loop() -> None:
                    log.info("[startup] gateway: whatsapp loop started")
                    try:
                        while True:
                            try:
                                msg = await whatsapp_adapter.receive()
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:  # noqa: BLE001 — top-level loop guard
                                log.error(
                                    "[startup] gateway: whatsapp receive failed — sleeping then retrying",
                                    exc_info=exc,
                                )
                                await asyncio.sleep(2.0)
                                continue
                            try:
                                log.info(
                                    "[startup] gateway: whatsapp message received",
                                    extra={"_fields": {"session_id": msg.session_id, "text_len": len(msg.text)}},
                                )
                                decision = scanner.scan(msg)
                                input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
                                # A numbered consent reply ("reply N") resolves the
                                # parked consent Future BEFORE clarify/intake — else
                                # it would be mistaken for a fresh turn.
                                if await whatsapp_consent_prompter.resolve_reply(
                                    msg.session_id, input_text
                                ):
                                    continue
                                consumed, input_text = await whatsapp_pump.resolve_or_rewrite(
                                    session_id=msg.session_id, channel=msg.channel,
                                    route=decision.route, target=decision.target, input_text=input_text,
                                )
                                if consumed:
                                    continue
                                await _intake(whatsapp_pump, whatsapp_adapter, msg, decision, input_text)
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:  # noqa: BLE001 — top-level loop guard
                                log.error(
                                    "[startup] gateway: whatsapp message processing failed — continuing",
                                    exc_info=exc,
                                    extra={"_fields": {"session_id": getattr(msg, "session_id", "?")}},
                                )
                                with contextlib.suppress(Exception):
                                    stream_registry.remove(getattr(msg, "trace_id", ""))
                                continue
                    except asyncio.CancelledError:
                        log.info("[startup] gateway: whatsapp loop cancelled")
                        raise

                whatsapp_loop_task = asyncio.create_task(_whatsapp_loop())
                log.info("[startup] gateway: WhatsApp adapter started")
            except Exception as exc:
                log.error(
                    "[startup] gateway: WhatsApp adapter start failed — skipping",
                    exc_info=exc,
                )
        else:
            log.info("[startup] gateway: WhatsApp not enabled — skipping")

        # 4. STEP — start the CLI loop and block on the adapter
        log.info("[startup] gateway: starting CLI adapter")
        loop_task = asyncio.create_task(_message_loop())
        # Recover the scheduler's durable state from the prior run BEFORE the poll
        # loop starts: reap jobs left 'running' by a crash and replay/realarm overdue
        # ones, so an assigned task survives a restart instead of wedging forever.
        # Fail-open: a recovery error must NOT block startup — the scheduler still runs.
        # NOTE: a replay_missed=True job dispatches its handler INLINE here (before the
        # watchdog notify below), so keep such handlers light or background them if
        # replay handlers ever become heavy — else they delay startup readiness.
        try:
            recovered = await scheduler_components.scheduler.recover()
            log.info(
                "[startup] gateway: scheduler recovered prior state",
                extra={"_fields": {"replayed": recovered}},
            )
        except Exception as exc:
            log.error(
                "[startup] gateway: scheduler.recover() failed — starting anyway",
                exc_info=exc,
                extra={"_fields": {}},
            )
        # Durable-task recovery (B4): reap tasks left 'running' OR 'recovering' by
        # a crash (at startup the prior process is dead, so both are orphans). The
        # AWAITED fast pass atomically claims each (CAS) and reconstructs its
        # PipelineState from the persisted ReAct checkpoint; each task's actual
        # resume DRIVE then runs as a BACKGROUND task so the gateway becomes
        # available immediately instead of blocking N x a full ReAct drive.
        # Committed side-effects replay from the ledger (exactly-once survives the
        # crash) and the runner finalizes through its idempotent terminal-status
        # guard. Recovery is fail-OPEN per task, each background drive is fail-OPEN,
        # and this whole block is fail-OPEN, so a recovery error never blocks
        # startup; the non-durable path is unaffected (no orphans => no-op). The
        # returned recoverer is held in a local that OUTLIVES the await below: it
        # owns the strong refs to the in-flight drives, so they are not GC'd.
        durable_recoverer = None
        try:
            from stackowl.pipeline.durable.recovery import recover_durable_tasks

            durable_recoverer = await recover_durable_tasks(db_pool, backend)
            log.info(
                "[startup] gateway: launched %d durable-task recoveries in background",
                durable_recoverer.launched,
                extra={"_fields": {"launched": durable_recoverer.launched}},
            )
        except Exception as exc:
            log.error(
                "[startup] gateway: durable-task recovery failed — starting anyway",
                exc_info=exc,
                extra={"_fields": {}},
            )
        # Start the scheduler under Supervisor so all registered handlers
        # (browser, dream worker, fact extraction, notification digest,
        # morning brief, etc.) actually dispatch.
        scheduler_task = asyncio.create_task(scheduler_components.supervisor.start())

        # F144 — cooperative shutdown: route SIGTERM/SIGINT to a stop_event so the
        # graceful teardown below actually runs (the old handler raised SystemExit,
        # bypassing it → orphaned children + leaked DB handle). POSIX uses the loop's
        # signal handler; Windows (no add_signal_handler) falls back to signal.signal
        # that ONLY trips the event (never raises SystemExit), with a warning.
        loop = asyncio.get_running_loop()
        # stop_event was created earlier (before register_all_commands) so /bye
        # can trip the SAME event the signal handlers below set.
        self._shutting_down = False

        def _request_stop(signame: str) -> None:
            if self._shutting_down:  # idempotent under double-signal (impatient Ctrl-C×2)
                log.warning("[startup] gateway: %s during shutdown — ignored", signame)
                return
            self._shutting_down = True
            log.info("[startup] gateway: %s received — cooperative shutdown", signame)
            stop_event.set()

        signals = [signal.SIGINT]
        if hasattr(signal, "SIGTERM"):
            signals.append(signal.SIGTERM)
        for sig in signals:
            try:
                loop.add_signal_handler(sig, _request_stop, sig.name)
            except NotImplementedError:
                # Windows: add_signal_handler is unsupported. Register a plain
                # handler that only trips the event from the loop thread; NEVER
                # raise SystemExit (that is HOW F144's bug existed).
                log.warning(
                    "[startup] gateway: add_signal_handler unsupported (%s) — "
                    "using threadsafe fallback",
                    sig.name,
                )

                def _win_handler(signum: int, frame: object, _n: str = sig.name) -> None:
                    loop.call_soon_threadsafe(_request_stop, _n)

                signal.signal(sig, _win_handler)

        # F142 — start the REAL recurring systemd watchdog (self-skips off-systemd).
        watchdog = WatchdogService()
        watchdog.start()
        # READY=1 ONCE, AFTER all assembly is done and we are about to serve — never
        # earlier (premature READY would let systemd start dependents while startup
        # could still fail). No-op off-systemd.
        watchdog.send_ready()
        try:
            await _run_until_signal(adapter, stop_event)
        finally:
            # F142 — stop the recurring watchdog ping FIRST so it cannot keep telling
            # systemd "healthy" during a wedged teardown (masking the very bug).
            with contextlib.suppress(Exception):
                watchdog.stop()
            # Stop the live-config watcher daemon thread (guarded so a shutdown
            # never raises out of the finally block).
            if config_watcher is not None:
                with contextlib.suppress(Exception):
                    config_watcher.stop()
            # E5 — drop any pending clarifies (wakes parked blocking waiters so
            # their turns end cleanly rather than hanging on the park timeout).
            with contextlib.suppress(Exception):
                clarify_gateway.clear_all()
            # E8-S3 — clear every named session (draining each mailbox) so no
            # session or its A2A mailbox outlives the process.
            with contextlib.suppress(Exception):
                session_registry.clear_all()
            # E9-S0 — terminate every supervised OS process (and checkpoint) so no
            # background process outlives the gateway.
            with contextlib.suppress(Exception):
                await process_registry.clear_all()
            if telegram_loop_task is not None:
                telegram_loop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await telegram_loop_task
            if telegram_adapter is not None:
                with contextlib.suppress(Exception):
                    await telegram_adapter.stop()
            # Slack shutdown (mirrors Telegram): cancel the message loop, stop the
            # Socket Mode connection (cancel the background socket task + close the
            # handler), then stop the adapter. All guarded so a shutdown never
            # raises out of the finally block.
            if slack_loop_task is not None:
                slack_loop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await slack_loop_task
            if slack_socket_task is not None:
                slack_socket_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await slack_socket_task
            if slack_socket_handler is not None:
                with contextlib.suppress(Exception):
                    await slack_socket_handler.close_async()  # type: ignore[no-untyped-call]
            if slack_adapter is not None and hasattr(slack_adapter, "stop"):
                with contextlib.suppress(Exception):
                    await slack_adapter.stop()
            # Discord shutdown (mirrors Telegram): cancel the message loop, then
            # stop the adapter. Guarded so a shutdown never raises out of finally.
            if discord_loop_task is not None:
                discord_loop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await discord_loop_task
            if discord_adapter is not None and hasattr(discord_adapter, "stop"):
                with contextlib.suppress(Exception):
                    await discord_adapter.stop()
            # WhatsApp shutdown (mirrors Telegram): cancel the message loop, then
            # stop the adapter (cancels its poll loop + closes the browser).
            if whatsapp_loop_task is not None:
                whatsapp_loop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await whatsapp_loop_task
            if whatsapp_adapter is not None and hasattr(whatsapp_adapter, "stop"):
                with contextlib.suppress(Exception):
                    await whatsapp_adapter.stop()
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await loop_task
            scheduler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await scheduler_task
            if mcp_task is not None:
                mcp_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await mcp_task
            if browser_sessions is not None:
                with contextlib.suppress(Exception):
                    await browser_sessions.stop_sweep_loop()
                with contextlib.suppress(Exception):
                    await browser_sessions.close_all()
            if browser_runtime is not None:
                with contextlib.suppress(Exception):
                    await browser_runtime.stop()
            # B4 — let any in-flight background durable recoveries finish (or be
            # awaited) BEFORE the pool closes, so a drive never writes through a
            # dead handle. drain() is itself fail-open (drives are fail-open).
            if durable_recoverer is not None:
                with contextlib.suppress(Exception):
                    await durable_recoverer.drain()
            # F067 — shut the dedicated Kuzu worker thread down cleanly so no
            # thread outlives the gateway (guarded; teardown never raises).
            # DUR-5 / F069 — kuzu_adapter is None when the graph layer degraded.
            if kuzu_adapter is not None:
                with contextlib.suppress(Exception):
                    await kuzu_adapter.aclose()
            with contextlib.suppress(Exception):
                await db_pool.close()
            # F144 — remove the PID file LAST (after the DB pool is closed) so a
            # racing `stackowl serve` never sees "not running" while this process
            # still holds the DB lock. Moved out of the old SystemExit handler.
            with contextlib.suppress(Exception):
                _pid_path().unlink(missing_ok=True)
                log.info("[startup] gateway: PID file removed in shutdown finally")

        # 5. EXIT
        log.info("[startup] gateway: adapter exited — shutdown complete")

    def _write_pid(self) -> None:
        pid = os.getpid()
        pid_path = _pid_path()
        if pid_path.exists():
            try:
                existing = int(pid_path.read_text(encoding="utf-8").strip())
                log.warning("[startup] WARNING — stale PID file detected (PID %d)", existing)
            except Exception as exc:
                log.warning("[startup] could not read stale PID file: %s", exc)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(pid), encoding="utf-8")
        log.info("[startup] PID %d written to %s", pid, pid_path)
        self._register_pid_cleanup(pid_path)

    def _register_pid_cleanup(self, pid_path: Path) -> None:
        """Register an ``atexit`` backstop that removes the PID file.

        F144: the SIGTERM/SIGINT path is now handled cooperatively inside
        ``_phase_gateway`` (signal → stop_event → graceful teardown → PID removed
        LAST in the finally). This handler no longer touches signals and no longer
        raises ``SystemExit`` — it is only a last-resort cleanup for an abnormal
        exit that skips the gateway finally (e.g. a hard crash before serving)."""
        import atexit

        def _remove_pid() -> None:
            with contextlib.suppress(Exception):
                pid_path.unlink(missing_ok=True)

        atexit.register(_remove_pid)
