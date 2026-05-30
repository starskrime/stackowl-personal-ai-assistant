"""StartupOrchestrator — 5-phase boot sequence with PID file and dry-run support."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from pathlib import Path

from stackowl.config.settings import Settings
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool, default_db_path
from stackowl.exceptions import ConfigurationError, StartupError
from stackowl.paths import StackowlHome
from stackowl.startup.browser_probe import BrowserProbe, BrowserProbeResult
from stackowl.startup.fs_probe import FilesystemProbe
from stackowl.startup.provider_probe import ProviderProbe
from stackowl.startup.watchdog import KeepAlive, WatchdogSec

log = logging.getLogger("stackowl.startup")


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


def _pid_path() -> Path:
    return StackowlHome.pid_file()


class StartupOrchestrator:
    """Boots StackOwl through 6 named phases; raises StartupError on any failure."""

    def __init__(self, dry_run: bool = False) -> None:
        self._dry_run = dry_run
        self._settings: Settings | None = None
        self._browser_probe_result: BrowserProbeResult | None = None

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

        # Write PID and signal ready BEFORE the blocking gateway phase
        if not self._dry_run:
            self._write_pid()
            WatchdogSec().notify()
            KeepAlive().register()

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
        if self._dry_run:
            log.info("[startup] reconciler: dry_run — skipping migration application")
            return
        db_path = default_db_path()
        runner = MigrationRunner(db_path=db_path)
        runner.run()

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
        from stackowl.commands.registry import CommandRegistry, load_builtin_commands
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
        owl_registry = OwlRegistry.from_settings(self._settings)
        owl_registry.register_builtin_personas()
        load_builtin_commands()
        db_pool = DbPool(default_db_path())
        await db_pool.open()
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
        )
        log.info(
            "[startup] gateway: skills loaded",
            extra={"_fields": {"count": len(skills_components.loaded)}},
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
        # `/skill` slash command — Commit 3, sub-phase 3b.
        from stackowl.commands.skill_command import SkillCommand

        SkillCommand.create_and_register(
            store=skills_components.store,
            loader=skills_components.loader,
            skills_root=StackowlHome.skills_dir(),
            embedding_registry=memory_components.embedding_registry,
        )
        audit_logger = AuditLogger(default_db_path())

        # Notifications subsystem assembly — router singleton + scheduled
        # digest job + router-dependent slash commands. See plan
        # gleaming-finding-puppy.md Commit C. Focus mode persists across
        # restarts via PreferenceStore.
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
        from stackowl.tools.consent import ConsentPolicy, RoutingPrompter, TtyConsentPrompter
        from stackowl.tools.registry import ConsequentialActionGate
        from stackowl.tui.i18n import register_translations

        # Consent button/label catalog — English copy lives here (i18n catalog is
        # the one place English belongs); other locales can be registered later.
        register_translations(
            "en",
            {
                "consent.prompt.title": "⚠ Approval needed",
                "consent.btn.approve_once": "✅ Approve once",
                "consent.btn.deny": "🚫 Deny",
                "consent.btn.approve_session": "✅ Approve for this session",
                "consent.btn.trust_window": "🕒 Trust for 15 min",
            },
        )

        consent_routing = RoutingPrompter()
        consent_routing.register("cli", TtyConsentPrompter())
        consent_gate = ConsequentialActionGate(
            ConsentPolicy(prompter=consent_routing, audit_logger=audit_logger)
        )

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

        services = StepServices(
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
            event_bus=event_bus,
            skill_store=skills_components.store,
            embedding_registry=memory_components.embedding_registry,
            lessons_index=memory_components.lessons_index,
            heuristic_store=_build_heuristic_store(db_pool),
            consent_gate=consent_gate,
            clarify_gateway=clarify_gateway,
            web_search_registry=web_search_registry,
        )
        backend = create_backend(self._settings.orchestrator.backend, services=services)
        parliament = ParliamentOrchestrator(
            backend=backend,
            session_store=SessionStore(db_pool),
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
        )

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

        command_names = [c.command for c in CommandRegistry.instance().list()]
        owl_names = [m.name for m in owl_registry.list()]
        tui_components = TuiAssembly.build(
            event_bus=event_bus,
            command_names=command_names,
            owl_names=owl_names,
            ui_settings=self._settings.ui,
        )
        adapter = CLIAdapter(
            tui_components=tui_components, event_bus=event_bus,
        )
        # E5 — let the clarify gateway deliver questions back over the CLI.
        clarify_gateway.register_adapter("cli", adapter)

        # 2. DECISION — define the message processing loop
        async def _deliver_parliament(topic: str, owl_names: list[str], session_id: str) -> None:
            """Run parliament and deliver the synthesis to the stream writer."""
            try:
                session = await parliament.run(topic=topic, owl_names=owl_names, session_id=session_id)
                synthesis = session.synthesis or "Parliament session completed with no synthesis."
            except Exception as exc:
                log.error("[startup] gateway: parliament session failed", exc_info=exc)
                synthesis = f"Parliament error: {exc}"
            writer = stream_registry.get_writer(session_id)
            if writer is not None:
                await writer.write(ResponseChunk(
                    content=synthesis, is_final=False, chunk_index=0,
                    trace_id=session_id, owl_name="parliament",
                ))
                await writer.close()

        async def _deliver_command_stub(cmd: str, session_id: str, state: PipelineState, args: str) -> None:
            """Dispatch a slash command and stream its reply back to the user."""
            registry = CommandRegistry.instance()
            writer = stream_registry.get_writer(session_id)
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
                    trace_id=session_id, owl_name="system",
                ))
                await writer.close()

        # E5 — clarify-aware turn dispatch. Each channel loop owns a ClarifyPump
        # (its own in-flight map): it decouples adapter.send from receive so a
        # parked clarify turn doesn't deadlock the loop, intercepts replies into
        # their parked turn, and serializes same-session slot reuse. See
        # stackowl.gateway.clarify_pump.ClarifyPump.
        from stackowl.gateway.clarify_pump import ClarifyPump

        cli_pump = ClarifyPump(clarify_gateway, stream_registry, clarify_classifier)

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
                        # Don't clobber a still-delivering same-session turn.
                        await cli_pump.serialize_prior(msg.session_id)
                        writer, reader = stream_registry.create(msg.session_id)
                        if decision.route == "parliament" and decision.parliament_owls:
                            log.info(
                                "[startup] gateway: routing to parliament",
                                extra={"_fields": {"owls": decision.parliament_owls, "session_id": msg.session_id}},
                            )
                            producer: asyncio.Task[object] = asyncio.create_task(
                                _deliver_parliament(input_text, decision.parliament_owls, msg.session_id)
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
                            )
                            cmd_args = input_text.split(" ", 1)[1] if " " in input_text else ""
                            producer = asyncio.create_task(
                                _deliver_command_stub(decision.target, msg.session_id, cmd_state, cmd_args)
                            )
                        else:
                            state = PipelineState(
                                trace_id=msg.trace_id,
                                session_id=msg.session_id,
                                input_text=input_text,
                                channel=msg.channel,
                                owl_name=decision.target,
                                pipeline_step="start",
                                interactive=True,  # real user turn on the CLI channel
                            )
                            producer = asyncio.create_task(backend.run(state))
                        producer.add_done_callback(_log_pipeline_crash)
                        # Decoupled send: frees the loop so a parked clarify turn
                        # can receive its answer; the pump closes the writer if the
                        # producer crashes so the send can never wedge the session.
                        cli_pump.spawn_send(
                            channel_adapter=adapter, reader=reader,
                            session_id=msg.session_id, producer=producer, writer=writer,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 — top-level loop guard
                        log.error(
                            "[startup] gateway: message processing failed — continuing",
                            exc_info=exc,
                            extra={"_fields": {"session_id": getattr(msg, "session_id", "?")}},
                        )
                        # Clean up the stream so it doesn't leak.
                        with contextlib.suppress(Exception):
                            stream_registry.remove(msg.session_id)
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
                                await tg_pump.serialize_prior(msg.session_id)
                                writer, reader = stream_registry.create(msg.session_id)
                                if decision.route == "parliament" and decision.parliament_owls:
                                    log.info(
                                        "[startup] gateway: routing to parliament (telegram)",
                                        extra={"_fields": {
                                            "owls": decision.parliament_owls,
                                            "session_id": msg.session_id,
                                        }},
                                    )
                                    tg_producer: asyncio.Task[object] = asyncio.create_task(
                                        _deliver_parliament(input_text, decision.parliament_owls, msg.session_id)
                                    )
                                elif decision.route == "command":
                                    log.info(
                                        "[startup] gateway: command route (telegram)",
                                        extra={"_fields": {"cmd": decision.target, "session_id": msg.session_id}},
                                    )
                                    tg_cmd_state = PipelineState(
                                        trace_id=msg.trace_id,
                                        session_id=msg.session_id,
                                        input_text=input_text,
                                        channel=msg.channel,
                                        owl_name="system",
                                        pipeline_step="start",
                                        interactive=True,  # real user typed a slash command
                                    )
                                    tg_cmd_args = input_text.split(" ", 1)[1] if " " in input_text else ""
                                    tg_producer = asyncio.create_task(_deliver_command_stub(
                                        decision.target, msg.session_id, tg_cmd_state, tg_cmd_args,
                                    ))
                                else:
                                    state = PipelineState(
                                        trace_id=msg.trace_id,
                                        session_id=msg.session_id,
                                        input_text=input_text,
                                        channel=msg.channel,
                                        owl_name=decision.target,
                                        pipeline_step="start",
                                        interactive=True,  # real user turn on the Telegram channel
                                    )
                                    tg_producer = asyncio.create_task(backend.run(state))
                                tg_producer.add_done_callback(_log_pipeline_crash)
                                tg_pump.spawn_send(
                                    channel_adapter=telegram_adapter, reader=reader,
                                    session_id=msg.session_id, producer=tg_producer, writer=writer,
                                )
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:  # noqa: BLE001 — top-level loop guard
                                log.error(
                                    "[startup] gateway: telegram message processing failed — continuing",
                                    exc_info=exc,
                                    extra={"_fields": {"session_id": getattr(msg, "session_id", "?")}},
                                )
                                with contextlib.suppress(Exception):
                                    stream_registry.remove(msg.session_id)
                                continue
                    except asyncio.CancelledError:
                        log.info("[startup] gateway: telegram loop cancelled")
                        raise

                telegram_loop_task = asyncio.create_task(_telegram_loop())
                log.info("[startup] gateway: Telegram adapter started")
        else:
            log.info("[startup] gateway: no Telegram bot_token — skipping")

        # 4. STEP — start the CLI loop and block on the adapter
        log.info("[startup] gateway: starting CLI adapter")
        loop_task = asyncio.create_task(_message_loop())
        # Start the scheduler under Supervisor so all registered handlers
        # (browser, dream worker, fact extraction, notification digest,
        # morning brief, etc.) actually dispatch.
        scheduler_task = asyncio.create_task(scheduler_components.supervisor.start())
        WatchdogSec().notify()
        try:
            await adapter.run()
        finally:
            # E5 — drop any pending clarifies (wakes parked blocking waiters so
            # their turns end cleanly rather than hanging on the park timeout).
            with contextlib.suppress(Exception):
                clarify_gateway.clear_all()
            if telegram_loop_task is not None:
                telegram_loop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await telegram_loop_task
            if telegram_adapter is not None:
                with contextlib.suppress(Exception):
                    await telegram_adapter.stop()
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
            with contextlib.suppress(Exception):
                await db_pool.close()

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
        def _cleanup(signum: int, frame: object) -> None:
            pid_path.unlink(missing_ok=True)
            log.info("[startup] PID file removed on signal %d", signum)
            raise SystemExit(0)

        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _cleanup)
        signal.signal(signal.SIGINT, _cleanup)
