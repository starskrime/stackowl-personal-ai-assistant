"""StartupOrchestrator — 5-phase boot sequence with PID file and dry-run support."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import signal
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from stackowl.config.settings import Settings
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool, default_db_path
from stackowl.exceptions import ConfigurationError, StartupError
from stackowl.paths import StackowlHome
from stackowl.runtime.turn_client import IngressHandler, LocalTurnClient, TurnClient
from stackowl.service.watchdog import WatchdogService
from stackowl.startup.browser_probe import BrowserProbe, BrowserProbeResult
from stackowl.startup.fs_probe import FilesystemProbe
from stackowl.startup.provider_probe import ProviderProbe
from stackowl.tenancy.identity import load_identity_resolver

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
    from stackowl.channels.telegram.voice_confirm import PendingTranscriptStore
    from stackowl.health.aggregator import HealthAggregator
    from stackowl.mcp.client import McpClient
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


# How long the gateway waits for the core's first socket connection before
# declaring boot failure (Phase 5). Generous — the core boots the full pipeline
# (providers/memory/skills/MCP/browser) before it connects.
_CORE_BOOT_TIMEOUT_S = 120.0


async def _supervise_core(
    proc_holder: dict[str, asyncio.subprocess.Process],
    socket_path: Path | None,
    stop_event: asyncio.Event,
    first_conn_event: asyncio.Event | None = None,
    on_crash: Callable[[int | None], Awaitable[None]] | None = None,
) -> None:
    """Phase 5 — respawn the core if it *crashes* (exits unexpectedly).

    A code-change restart is an ``os.execv`` IN the core: the PID is unchanged, so
    this ``wait()`` does NOT return — the gateway just re-accepts the reconnect.
    It returns only on a genuine crash, in which case we respawn with capped
    exponential backoff until the gateway itself is shutting down. The durable
    IpcServer listener never drops, so the fresh core simply reconnects.

    F-36 — after a respawn we now AWAIT the fresh core's reconnect with the same
    bounded ``_CORE_BOOT_TIMEOUT_S`` first boot uses (via ``first_conn_event``,
    which the accept handler sets on every core connection). A respawned core that
    boots but never connects back would otherwise leave the gateway buffering
    forever; on reconnect-timeout we surface a LOUD operator failure and stop
    supervising (set ``stop_event`` to bring the gateway down) rather than buffer
    silently. ``first_conn_event`` is cleared BEFORE each respawn so we wait for
    the NEW connection, not a stale set from the previous core.
    """
    from stackowl.runtime.supervisor import spawn_core

    if socket_path is None:
        return
    backoff = 1.0
    while not stop_event.is_set():
        proc = proc_holder.get("proc")
        if proc is None:
            return
        rc = await proc.wait()
        if stop_event.is_set():
            return
        log.warning(
            "[startup] gateway: core exited unexpectedly — respawning (rc=%s, backoff=%.1fs)",
            rc,
            backoff,
        )
        # F-39 — a crash was previously silent to the user (operator log only). Emit
        # a user-visible notice so the human knows the assistant restarted itself.
        # Fail-safe: a notice failure must never break the respawn loop.
        if on_crash is not None:
            try:
                await on_crash(rc)
            except Exception as exc:  # noqa: BLE001 — notice is best-effort
                log.error(
                    "[startup] gateway: crash notice failed", exc_info=exc
                )
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30.0)
        if stop_event.is_set():
            return
        # Clear BEFORE respawn so we wait for the fresh core's connection, not a
        # stale set from the core that just died.
        if first_conn_event is not None:
            first_conn_event.clear()
        try:
            proc_holder["proc"] = await spawn_core(socket_path)
        except Exception as exc:  # spawn itself failed — log and retry on next loop
            log.error("[startup] gateway: core respawn failed to spawn", exc_info=exc)
            continue
        # F-36 — verify the respawned core actually reconnects, bounded like boot.
        if first_conn_event is not None:
            try:
                await asyncio.wait_for(
                    first_conn_event.wait(), timeout=_CORE_BOOT_TIMEOUT_S
                )
                log.info("[startup] gateway: respawned core reconnected — link restored")
            except TimeoutError:
                if stop_event.is_set():
                    return
                log.error(
                    "[startup] ★ CORE RESPAWN DID NOT RECONNECT ★ within %.0fs — the "
                    "gateway will NOT keep buffering forever; shutting down so an "
                    "external manager / operator can recover.",
                    _CORE_BOOT_TIMEOUT_S,
                )
                stop_event.set()
                return


def detect_service_manager(
    env: dict[str, str] | None = None, platform: str | None = None
) -> str | None:
    """Best-effort detect a host service manager that can auto-restart this process.

    F-88 — in the default ``mono`` role there is NO in-process crash supervisor (a
    full mono supervisor is a larger change); an unhandled crash relies on an
    EXTERNAL service manager with ``Restart=always``. We can't read that external
    unit's config, but we CAN detect whether a recognised manager is supervising
    us via the well-known environment markers each one exports, plus the macOS
    launchd case. Returns a short manager name (e.g. ``"systemd"``) or ``None`` if
    none is detected — the caller warns LOUDLY on ``None`` so an operator running
    bare (``python -m stackowl serve`` in a shell) knows a crash will NOT restart.

    This is detection, not a guarantee of ``Restart=always``: a detected manager
    still might be configured ``Restart=no``. The warning is calibrated to that —
    it flags the ABSENCE of any supervisor, the common real-world footgun.
    """
    e = os.environ if env is None else env
    plat = sys.platform if platform is None else platform
    # systemd exports these for a supervised unit (notify/watchdog or just run).
    if e.get("INVOCATION_ID") or e.get("NOTIFY_SOCKET") or e.get("WATCHDOG_USEC"):
        return "systemd"
    # Generic supervisors that export an identifying marker.
    if e.get("SUPERVISOR_ENABLED") or e.get("SUPERVISOR_PROCESS_NAME"):
        return "supervisord"
    if e.get("PM2_HOME") or e.get("pm_id"):
        return "pm2"
    if e.get("RUNIT_SERVICE") or e.get("S6_SERVICE_PATH"):
        return "runit/s6"
    # macOS launchd marks managed jobs with an XPC service name.
    if plat == "darwin" and e.get("XPC_SERVICE_NAME") not in (None, "", "0"):
        return "launchd"
    return None


def _build_liveness_aggregator() -> HealthAggregator:
    """A minimal HealthAggregator for the F-85 watchdog liveness gate.

    Only the LOCAL critical subsystems whose failure means the process genuinely
    cannot serve: the db pool and the data/log filesystem. Deliberately NOT the
    network provider contributors (a provider outage must not kill the process)
    nor browser/resilience (live-runtime refs, may report 'not constructed')."""
    from stackowl.health.aggregator import HealthAggregator
    from stackowl.health.contributors import DbContributor, FilesystemContributor
    from stackowl.startup.fs_probe import _data_dir, _log_dir

    agg = HealthAggregator()
    agg.register(DbContributor(default_db_path()))
    agg.register(FilesystemContributor(_data_dir(), _log_dir()))
    return agg


def _pid_path() -> Path:
    return StackowlHome.pid_file()


def _resolve_socket_path(settings: Settings) -> Path:
    """The gateway<->core socket path: explicit override, else the home default.

    The core subprocess also honours ``STACKOWL_CORE_SOCKET`` (set by the
    supervisor when it spawns the core) so both halves resolve the same endpoint
    even if config derivation differs; that env var takes precedence here.
    """
    env_override = os.environ.get("STACKOWL_CORE_SOCKET")
    if env_override:
        return Path(env_override)
    configured = settings.runtime.socket_path
    if configured:
        return Path(configured)
    return StackowlHome.core_socket()


class StartupOrchestrator:
    """Boots StackOwl through 6 named phases; raises StartupError on any failure."""

    def __init__(self, dry_run: bool = False, *, role: str = "mono") -> None:
        self._dry_run = dry_run
        # Process role for the two-process split (runtime.split_process). "mono"
        # (default) is the single-process monolith — byte-identical to baseline,
        # every split-only branch in _phase_gateway is guarded on this. "gateway"
        # is the durable client-facing half; "core" is the restartable agent half.
        if role not in ("mono", "gateway", "core"):
            raise ValueError(f"invalid orchestrator role: {role!r}")
        self._role = role
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

        # F-86 — fail-closed reachability census (advisory; never refuses READY).
        if not self._dry_run:
            await self._phase_reachability_census()

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

    async def _phase_reachability_census(self) -> None:
        """F-86 / ADR-4 — the reachability invariant: run the fail-closed self-audit at
        boot and, under ``reachability_enforcement="block"``, REFUSE READY when a
        registered capability is unreachable on the default path.

        :func:`run_census` already existed but nothing invoked it on the boot path, so a
        subsystem that ships green-but-dead-on-default-path was never caught (the census
        was itself an unreached half-edge — the bug eating its own tail). Two modes:

        * ``"warn"`` (default ⇒ byte-identical): a definitive census failure logs a LOUD
          degraded alert but the service starts anyway.
        * ``"block"``: a definitive census failure raises :class:`StartupError` — the
          dangling half-edge fails the boot, not the user. The reachability invariant.

        A broken AUDITOR (the census machinery itself raising) is advisory in BOTH modes —
        only a definitive ``census_passes()==False`` verdict blocks. So a probe
        false-negative or a transient cannot brick a boot; only a real dead edge does.
        """
        enforcement = (
            self._settings.reachability_enforcement if self._settings else "warn"
        )
        try:
            # Importing the probes module self-registers every probe.
            import stackowl.health.reachability.probes  # noqa: F401
            from stackowl.health.reachability import census_passes, run_census

            results = await run_census()
        except Exception as exc:
            # Advisory: a broken census auditor must never block boot (even in block mode).
            log.error(
                "[startup] reachability census: audit itself failed — skipping",
                exc_info=exc,
            )
            return

        if census_passes(results):
            log.info(
                "[startup] reachability census: ok — %d subsystems reachable",
                len(results),
            )
            return

        unreachable = [f"{r.name}: {r.detail}" for r in results if not r.reachable]
        if enforcement == "block":
            log.error(
                "[startup] ★ REACHABILITY CENSUS FAILED (block mode) ★ — REFUSING READY: "
                "%d registered subsystem(s) are NOT reachable on the default path: %s",
                len(unreachable),
                "; ".join(unreachable),
            )
            raise StartupError(
                0,
                "reachability",
                f"{len(unreachable)} registered capability(ies) unreachable on the "
                f"default path: {'; '.join(unreachable)}",
            )
        log.error(
            "[startup] ★ REACHABILITY CENSUS DEGRADED ★ — %d subsystem(s) "
            "dead on the default path; the service is starting ANYWAY (warn mode) but "
            "these are NOT reachable: %s",
            len(unreachable),
            "; ".join(unreachable),
        )

    async def _phase_gateway(self) -> None:
        """Start channel adapters and run the main message loop.

        Blocks until the CLI adapter exits (user closes the TUI).
        In dry_run mode, returns immediately after logging.
        """
        if self._dry_run:
            log.info("[startup] gateway: dry_run — skipping adapter start")
            return

        # F-88 — in mono there is no in-process crash supervisor (only the split
        # gateway role respawns the core). If NO external service manager is
        # supervising us, an unhandled crash means the process just dies. Warn
        # LOUDLY so an operator running bare in a shell knows. (gateway/core roles
        # are covered by the gateway's crash-respawn supervisor.)
        if self._role == "mono":
            manager = detect_service_manager()
            if manager is None:
                log.error(
                    "[startup] ★ NO SERVICE MANAGER DETECTED ★ — running mono with "
                    "no in-process crash supervisor AND no external manager "
                    "(systemd/launchd/supervisord/pm2/runit) with Restart=always. An "
                    "unhandled crash will NOT auto-restart. Run under a service unit "
                    "for resilience, or use the split gateway/core role.",
                )
            else:
                log.info(
                    "[startup] gateway: service manager detected (%s) — external "
                    "auto-restart available for mono",
                    manager,
                )

        from stackowl.audit.logger import AuditLogger
        from stackowl.channels.base import ChannelAdapter
        from stackowl.channels.cli_adapter import CLIAdapter
        from stackowl.channels.socket_adapter import SocketChannelAdapter
        from stackowl.commands.registry import CommandRegistry
        from stackowl.exceptions import CommandNotFoundError
        from stackowl.gateway.scanner import GatewayScanner
        from stackowl.ipc.client import IpcClient
        from stackowl.ipc.connection import FrameConnection
        from stackowl.ipc.frames import (
            ClarifyReplyFrame,
            ConsentResponseFrame,
            HelloFrame,
            IngressFrame,
            RestartNoticeFrame,
        )
        from stackowl.ipc.server import IpcServer
        from stackowl.ipc.stream_bridge import SocketStreamRegistry
        from stackowl.owls.registry import OwlRegistry
        from stackowl.parliament.orchestrator import ParliamentOrchestrator
        from stackowl.parliament.session_store import SessionStore
        from stackowl.pipeline.backends.factory import create_backend
        from stackowl.pipeline.services import StepServices, resolve_identity_key
        from stackowl.pipeline.state import PipelineState
        from stackowl.pipeline.streaming import ResponseChunk, StreamRegistry
        from stackowl.providers.registry import ProviderRegistry
        from stackowl.runtime.code_watcher import CodeWatcher
        from stackowl.runtime.drain import quiesce
        from stackowl.runtime.gateway_link import GatewayLink
        from stackowl.runtime.message_bridge import frame_to_ingress
        from stackowl.runtime.supervisor import spawn_core
        from stackowl.tools.browser.runtime import CamoufoxRuntime
        from stackowl.tools.browser.sessions import BrowserSessionRegistry
        from stackowl.tools.registry import ToolRegistry

        assert self._settings is not None

        # --- Two-process split bootstrap (runtime.split_process) ---------------
        # role == "mono" (default) skips ALL of this and runs the in-process
        # monolith byte-identically. CORE connects to the durable gateway as a
        # client; GATEWAY binds the socket and spawns the core subprocess. The
        # connection objects feed the role-guarded branches below (stream
        # registry, primary adapter, turn client, driver).
        core_conn: FrameConnection | None = None
        gateway_server: IpcServer | None = None
        gateway_socket_path: Path | None = None
        # Set when the first core connection lands (Phase 5 boot-timeout waits on it).
        gateway_first_conn_ready = asyncio.Event()
        # Holder so the crash-respawn supervisor and teardown share one handle that
        # a respawn can swap (execv keeps the PID, so this only changes on a crash).
        core_proc_holder: dict[str, asyncio.subprocess.Process] = {}
        gateway_link: GatewayLink | None = None
        # CORE self-restart wiring (Phase 3/4): a fired event drives a graceful
        # quiesce -> teardown -> os.execv; the flag survives the teardown finally.
        restart_event = asyncio.Event()
        restart_requested = {"value": False}
        _restart_grace = self._settings.runtime.auto_restart.grace_seconds
        code_watcher: CodeWatcher | None = None
        if self._role == "core":
            socket_path = _resolve_socket_path(self._settings)
            log.info(
                "[startup] core: connecting to gateway socket",
                extra={"_fields": {"socket_path": str(socket_path)}},
            )
            core_conn = await IpcClient(socket_path).connect()
            # Announce readiness so the gateway flushes any messages buffered while
            # this (possibly just-exec-replaced) core was booting.
            with contextlib.suppress(Exception):
                await core_conn.send(HelloFrame(core_pid=os.getpid()))
            log.info("[startup] core: connected to gateway")
        elif self._role == "gateway":
            gateway_socket_path = _resolve_socket_path(self._settings)

        # 1. ENTRY — build services
        log.info("[startup] gateway: building services")
        provider_registry = ProviderRegistry.from_settings(self._settings)
        stream_registry = (
            SocketStreamRegistry(core_conn)
            if self._role == "core" and core_conn is not None
            else StreamRegistry()
        )
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

        # WS-D command-sequence learning — durable per-owner "after A you usually
        # do B". Gated by ui.command_suggestions: None when off, so there is no
        # recording, no suggested lane, and the dropdown stays byte-identical to
        # the deterministic baseline (honesty spine). Built here (after the pool
        # opens, migration 0065 already applied in phase 1) so the command-stub
        # closure below can capture it.
        sequence_store = None
        if self._settings.ui.command_suggestions:
            from stackowl.commands.sequence_store import CommandSequenceStore

            sequence_store = CommandSequenceStore(db_pool)
            log.info("[startup] gateway: command-sequence learning enabled")

        from stackowl.owls.dna_authored import capture_authored_dna

        await capture_authored_dna(owl_registry, db_pool)
        from stackowl.owls.dna_hydrator import hydrate_dna

        await hydrate_dna(owl_registry, db_pool)
        # PA4b — re-attach synth-learned skills to their owning owls (durable
        # ownership survives restart, mirrors hydrate_dna). Fail-safe internally.
        from stackowl.owls.skill_ownership import hydrate_skill_ownership

        await hydrate_skill_ownership(owl_registry, db_pool)
        from stackowl.owls.owl_revalidator import revalidate_agent_owls

        revalidate_agent_owls(owl_registry)
        # Memory subsystem assembly — wires the entire consolidation stack
        # (bridge, preference store, Kuzu adapter, DreamWorker, FactExtractor)
        # via the MemoryAssembly factory. See plan: gleaming-finding-puppy.md
        # Commit A. Hard-fails on Kuzu init per operator-approved decision.
        from stackowl.memory.assembly import MemoryAssembly

        # ONE IdentityResolver shared by StepServices (preferences) and the
        # FactExtractor (fact staging). Built once here and threaded into both so
        # a live `settings_reloaded` alias edit (mutated in place via the
        # identity reload handler below) propagates to every durable-knowledge
        # consumer without a restart — and so we read Settings for it just once.
        identity_resolver = load_identity_resolver()
        memory_components = await MemoryAssembly.build(
            db=db_pool, settings=self._settings, provider_registry=provider_registry,
            identity_resolver=identity_resolver,
            # Kuzu is single-writer: in the gateway+core split only the CORE opens the
            # graph (it runs the pipeline + memory jobs that use it). The gateway routes
            # only, so it skips the open and avoids racing the core for the file lock
            # (the race made one process degrade to a None graph with a spurious ERROR
            # every boot). mono/core open it as before.
            open_graph=(self._role != "gateway"),
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
        # ADR-6 Task 8 — pre-initialize so the SchedulerAssembly.build() call below
        # can reference it unconditionally, mirroring browser_runtime's pattern.
        mcp_client: McpClient | None = None
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
        # GATEWAY skips the browser entirely — the core owns the browser runtime
        # and its scheduler handlers, so running it here would double them.
        if self._role != "gateway" and probe is not None and probe.binary_ok:
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
                from stackowl.scheduler.handlers.threshold_watch import (
                    register_threshold_watch_handler,
                )
                from stackowl.scheduler.handlers.website_watch import register_website_watch_handler

                watch_state_dir = browser_settings.browser_cache_dir / "watch"
                threshold_state_dir = browser_settings.browser_cache_dir / "threshold"
                screenshot_archive_dir = StackowlHome.knowledge_dir() / "screenshots"
                profile_backups_dir = StackowlHome.home() / "backups" / "browser-profiles"
                # WS-D — give the website_watch handler the SAME durable, exactly-
                # once delivery seam goal_execution/check_in/morning_brief use so a
                # detected change is actually pinged back to the chat the watch was
                # scheduled from (addressed from the job's durable target). The
                # ledger is constructed identically to SchedulerAssembly's. Wired
                # only when a real deliverer exists; absent it, a change is recorded
                # honestly without a send (never a fake "delivered").
                watch_job_deliverer = None
                if proactive_deliverer is not None:
                    from stackowl.notifications.delivery_ledger import DeliveryLedger
                    from stackowl.notifications.proactive_job import (
                        ProactiveJobDeliverer,
                    )

                    watch_job_deliverer = ProactiveJobDeliverer(
                        proactive_deliverer, DeliveryLedger(db=db_pool), settings=self._settings
                    )
                register_website_watch_handler(
                    browser_runtime, watch_state_dir, watch_job_deliverer
                )
                # ADR-C — threshold_watch shares the SAME runtime + durable delivery
                # seam as website_watch (its conditional sibling). on_demand, projected
                # from a scheduled owl's threshold trigger; no standing seed.
                register_threshold_watch_handler(
                    browser_runtime, threshold_state_dir, watch_job_deliverer
                )
                register_screenshot_archive_handler(browser_runtime, screenshot_archive_dir)
                register_browser_recycle_handler(browser_runtime, browser_sessions)
                register_browser_cache_eviction_handler(
                    browser_settings.browser_cache_dir, browser_settings.screenshots_dir,
                )
                register_credential_rotation_handler(
                    browser_runtime, browser_settings.browser_cache_dir / "credential_rotation",
                )
                register_profile_backup_handler(browser_settings.profiles_dir, profile_backups_dir)
                # WS-G — seed the LOCAL browser-maintenance jobs (profile_backup,
                # browser_recycle, browser_cache_eviction) so the poll loop actually
                # dispatches them. CO-LOCATED with the register_* calls above and
                # guarded by the same browser-available block: a browser-less box
                # neither registers NOR seeds them (never a seeded-but-unregistered
                # row that errors every poll). The two param-required handlers
                # (screenshot_archive, credential_rotation) are on_demand and are
                # deliberately NOT seeded here. db_pool is ready (WS-D already used
                # it for the DeliveryLedger above).
                from stackowl.scheduler.assembly import (
                    seed_browser_maintenance_schedules,
                )

                await seed_browser_maintenance_schedules(db_pool)
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

        # CORE role has no local consent UI (no TTY, no bot) — every channel's
        # consent round-trips to the durable gateway over the socket. One
        # SocketConsentPrompter serves all channels (it reads req.channel); it is
        # resolved by inbound ConsentResponseFrames in the core frame loop.
        socket_consent_prompter = None
        if self._role == "core" and core_conn is not None:
            from stackowl.runtime.socket_consent import SocketConsentPrompter

            socket_consent_prompter = SocketConsentPrompter(core_conn)
            for _chan in ("cli", "telegram", "slack", "discord", "whatsapp"):
                consent_routing.register(_chan, socket_consent_prompter)
            log.info("[startup] core: socket consent prompter registered (all channels)")

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
        # LS4 — the feedback-capture classifier (fast tier, fail-open→abstain). The
        # pipeline ``feedback`` step reads it off services to turn a reaction to the
        # last render into an aspect-scoped ``output_style`` preference write.
        from stackowl.interaction.feedback_classifier import FeedbackClassifier

        feedback_classifier = FeedbackClassifier(provider_registry)
        # PBC — overclaim trigger 3's retrieval-intent classifier (fast tier,
        # fail-safe→known). surface_overclaim_gate's wrapper reads it off services
        # to lazily judge whether a clean, non-delivering, non-conversational
        # turn's intent required a live lookup that never ran.
        from stackowl.interaction.retrieval_intent_classifier import (
            RetrievalIntentClassifier,
        )

        retrieval_intent_classifier = RetrievalIntentClassifier(provider_registry)
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
            from stackowl.startup.identity_reload import make_identity_reload_handler
            from stackowl.startup.provider_reload import make_settings_reload_handler

            event_bus.subscribe(
                "settings_reloaded", make_settings_reload_handler(provider_registry)
            )
            # LIVE identity hot-reload — an `identity.aliases` edit refreshes the
            # SHARED resolver in place (seen by both preferences + fact staging),
            # no restart. Mirrors the provider reload above on the same event.
            event_bus.subscribe(
                "settings_reloaded", make_identity_reload_handler(identity_resolver)
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

        # FR-9 — the sticky-routing cache. ONE DI singleton, no dependencies;
        # triage.py reads THIS instance off services to bypass the LLM
        # SecretaryRouter call on short, same-session follow-ups.
        from stackowl.owls.sticky_route_cache import StickyRouteCache

        sticky_route_cache = StickyRouteCache()

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
            feedback_classifier=feedback_classifier,
            retrieval_intent_classifier=retrieval_intent_classifier,
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
            identity_resolver=identity_resolver,
            sticky_route_cache=sticky_route_cache,
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
            # Phase L — heavy background jobs (dream_worker/kuzu_sync/critic/
            # reflection) defer to live user turns so they stop starving the box.
            turn_registry=turn_registry,
            # ADR-6 F-87 — the live browser runtime as a HealableResource so the
            # health loop can recycle + re-verify it (None in gateway role / when
            # the browser failed to start).
            browser_runtime=browser_runtime,
            # ADR-6 Task 8 — MCP client for health aggregation (None if not configured
            # or MCP failed to init — pre-initialized above, mirrors browser_runtime).
            mcp_client=mcp_client,
            # Task 4 — the real ConsequentialActionGate so SkillSynthesizerHandler's
            # gated skill-authoring writes can actually be approved via a configured
            # trust tier, instead of always failing closed on a None gate.
            consent_gate=consent_gate,
            # Task 7 — so IncidentEscalationHandler's "alternative" verdict
            # consumer can consult capability_substitution.find_substitute.
            tool_registry=tool_registry,
        )
        # Task 7 — thread the background-incident RCA verdicts onto the SAME
        # mutable ``services`` instance the live pipeline already reads (mirrors
        # the ``services.a2a_delegator = ...`` post-construction wiring above:
        # SchedulerAssembly.build() must run first to produce the handler).
        # surface_critical_failure reads this to enrich its apology/neutral-
        # fallback text with a one-line incident summary when a verified verdict
        # exists for the SAME failure_class this turn's critical step just hit —
        # reusing the EXISTING delivery_gate cascade, never a new one.
        _incident_handler = scheduler_components.incident_escalation_handler
        services.incident_verdict_lookup = lambda fc: next(
            (v for v in _incident_handler.verdicts.values() if v.verified and v.failure_class == fc),
            None,
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
            proactive_deliverer=proactive_deliverer,
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
        # GATEWAY skips the MCP server — the core owns it (one federation endpoint).
        if self._role != "gateway" and self._settings.mcp_server.enabled:
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

        # registry.list() yields SlashCommand INSTANCES, so each exposes its
        # static .meta (CommandMeta: grammar + sub-command tree). Sub-commands
        # are class metadata, not runtime state, so this one-shot startup
        # snapshot is authoritative for the lifetime of the TUI — the dropdown
        # never needs to re-reach into the registry per keystroke.
        _commands = CommandRegistry.instance().list()
        command_names = [c.command for c in _commands]
        command_infos = [
            CommandInfo(name=c.command, description=c.description, meta=c.meta)
            for c in _commands
        ]
        owl_names = [m.name for m in owl_registry.list()]

        # WS-D AI lanes for the local terminal. A stable CLI session id ties the
        # suggested-lane owner_key to the SAME identity the command-stub records
        # under (state.identity_key or session_id), so "after A you usually B"
        # surfaces within the session. Both lanes are None unless their config
        # flag is on → the dropdown stays byte-identical to the deterministic
        # baseline (honesty spine).
        cli_session_id = "cli-local"
        sequence_provider = None
        if sequence_store is not None:
            from stackowl.commands.sequence_store import SequenceSuggestionProvider

            owner_key = resolve_identity_key(services, cli_session_id) or cli_session_id
            sequence_provider = SequenceSuggestionProvider(sequence_store, owner_key)
        # ONE CommandResolver (indexed over the command tree) shared by the TUI
        # semantic panel (issue 2) and the pre-delivery NL→command hint (issue 3),
        # built only when at least one of those flags is on. Mirrors /find's
        # embeddings access (lexical-only fallback when no model). Both consumers
        # are gated independently below.
        command_resolver = None
        if (
            self._settings.ui.semantic_command_search
            or self._settings.ui.command_hints
        ):
            from stackowl.commands.resolver import CommandResolver

            _emb_registry = memory_components.embedding_registry
            provider = None
            semantic = False
            try:
                provider = _emb_registry.get()
                semantic = _emb_registry.is_semantic
            except Exception as exc:  # lexical-only fallback is fine
                log.debug(
                    "[startup] gateway: resolver embeddings unavailable", exc_info=exc
                )
            command_resolver = CommandResolver(provider, semantic=semantic)
            command_resolver.index(_commands)
            log.info(
                "[startup] gateway: command resolver built",
                extra={"_fields": {"semantic": semantic}},
            )
        semantic_resolver = (
            command_resolver if self._settings.ui.semantic_command_search else None
        )
        if self._settings.ui.command_hints:
            # Inject onto the SAME mutable StepServices the backend reads at
            # execute time (mirrors a2a_delegator wiring above).
            services.command_hint_resolver = command_resolver

        # CORE role has no terminal — its only "channel" is the socket to the
        # gateway, so it skips the TUI build entirely and uses a SocketChannelAdapter
        # as the primary (originating-channel "cli") adapter. mono/gateway build the
        # real Textual TUI exactly as before (byte-identical).
        adapter: ChannelAdapter
        if self._role == "core":
            assert core_conn is not None
            adapter = SocketChannelAdapter(core_conn, channel_name="cli")
        else:
            # Voice dictation (opt-in): build the mic recorder + shared STT selector
            # so the compose ctrl+r push-to-talk works. Disabled → both None and the
            # binding degrades to a status line (byte-identical baseline).
            tui_recorder = None
            tui_stt_selector = None
            if self._settings.transcription.enabled:
                from stackowl.media.stt.selector import SttSelector
                from stackowl.tui.voice.recorder import ShellMicRecorder

                tui_recorder = ShellMicRecorder()
                tui_stt_selector = SttSelector(self._settings.transcription)
                log.info("[startup] tui: voice dictation enabled")
            tui_components = TuiAssembly.build(
                event_bus=event_bus,
                command_names=command_names,
                command_infos=command_infos,
                owl_names=owl_names,
                ui_settings=self._settings.ui,
                sequence_provider=sequence_provider,
                semantic_resolver=semantic_resolver,
                recorder=tui_recorder,
                stt_selector=tui_stt_selector,
            )
            adapter = CLIAdapter(
                session_id=cli_session_id,
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
            dispatched_ok = False
            try:
                reply = await registry.dispatch(cmd, args, state)
                dispatched_ok = True
            except CommandNotFoundError:
                reply = f"Unknown slash command: '/{cmd}'. Try /help to see what's available."
                # Surface the commands they likely meant, using the same resolver
                # that powers /find (lexical, no model load). Never fatal.
                try:
                    from stackowl.commands.resolver import suggest_invocations
                    hits = await suggest_invocations(
                        f"{cmd} {args}".strip(), registry.list(), limit=3
                    )
                    if hits:
                        reply += "\n\nDid you mean:\n" + "\n".join(f"  {h}" for h in hits)
                except Exception as exc:  # suggestion is best-effort
                    log.debug("[startup] gateway: command suggestion failed", exc_info=exc)
            except Exception as exc:
                log.error("[startup] gateway: slash command failed", exc_info=exc)
                reply = f"Command '/{cmd}' failed: {exc}"
            # WS-D — learn the command sequence (best-effort, only on a real
            # successful dispatch; a `??` dry-run is skipped inside record_dispatch).
            # owner_key matches the per-turn identity the TUI provider reads back,
            # so within-session "after A you usually B" surfaces immediately.
            if dispatched_ok and sequence_store is not None:
                try:
                    from stackowl.commands.sequence_store import record_dispatch

                    cmd_obj = registry.get(cmd)
                    if cmd_obj is not None:
                        owner_key = state.identity_key or session_id
                        await record_dispatch(
                            sequence_store, cmd, cmd_obj.meta, args, owner_key
                        )
                except Exception as exc:  # learning is never load-bearing
                    log.debug(
                        "[startup] gateway: sequence record failed", exc_info=exc
                    )
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
                    identity_key=resolve_identity_key(services, msg.session_id),
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
                    identity_key=resolve_identity_key(services, msg.session_id),
                    # WS-D issue 3 — carry the scanner's fuzzy routing correction so
                    # the pre-delivery hint surfacer can show it (else it's dead).
                    route_suggestion=decision.suggestion,
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
                        is_direct=msg.is_direct,  # ADR-D — preserve the 1:1 gate on re-scan.
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

        async def _handle_ingress(
            pump: ClarifyPump,
            channel_adapter: _IntakeAdapter,
            msg: IngressMessage,
        ) -> None:
            """Shared receive-loop body: scan -> resolve clarify reply -> intake.

            The single gateway->core submission path, byte-identical across every
            channel loop. ``LocalTurnClient.submit`` dispatches it per
            ``msg.channel`` (the SocketTurnClient will instead serialise the
            message to an IngressFrame for a core process to run this body).
            """
            decision = scanner.scan(msg)
            input_text = (
                decision.stripped_text if decision.stripped_text is not None else msg.text
            )
            # E5 — a reply to a pending clarify resumes its turn (or seeds a
            # fresh resume turn in the turn-yield fallback).
            consumed, input_text = await pump.resolve_or_rewrite(
                session_id=msg.session_id, channel=msg.channel,
                route=decision.route, target=decision.target, input_text=input_text,
            )
            if consumed:
                return
            # §4.3 non-blocking intake: dispatch if idle, else enqueue FIFO +
            # instant-ack. The running turn's completion drains the next intake.
            await _intake(pump, channel_adapter, msg, decision, input_text)

        # The turn-submission seam. mono/core run the body in-process via
        # LocalTurnClient; GATEWAY routes it over the socket via GatewayLink
        # (submit forwards an IngressFrame to the core and spawns the unchanged
        # adapter.send over a demux reader). The core decides STEER/STOP/NEW
        # internally, so the gateway needs no steer/stop RPCs.
        turn_client: TurnClient
        if self._role == "gateway":
            assert gateway_socket_path is not None
            gateway_link = GatewayLink(
                {adapter.channel_name: adapter},
                event_bus=event_bus,
                consent_router=consent_routing,
            )
            turn_client = gateway_link

            async def _accept_core(conn: FrameConnection) -> None:
                # One call per accepted core connection. The FIRST establishes the
                # link; every later one is the fresh core after an exec-replace.
                # IpcServer closes the connection when this returns, so we route it
                # to EOF here, then drop+finalize so the next core can reattach.
                assert gateway_link is not None
                gateway_link.set_connection(conn)
                gateway_first_conn_ready.set()
                try:
                    await gateway_link.run(conn)
                finally:
                    gateway_link.drop_connection()
                    with contextlib.suppress(Exception):
                        await gateway_link.finalize()

            gateway_server = IpcServer(gateway_socket_path)
            await gateway_server.start(_accept_core)
            log.info(
                "[startup] gateway: socket bound — spawning core",
                extra={"_fields": {"socket_path": str(gateway_socket_path)}},
            )
            core_proc_holder["proc"] = await spawn_core(gateway_socket_path)
            # Phase 5 — bounded wait for the core's first connection so a core that
            # dies during boot surfaces as a startup failure instead of a hang.
            log.info("[startup] gateway: waiting for core to connect")
            try:
                await asyncio.wait_for(
                    gateway_first_conn_ready.wait(), timeout=_CORE_BOOT_TIMEOUT_S
                )
            except TimeoutError as exc:
                raise StartupError(
                    6, "gateway",
                    f"core did not connect within {_CORE_BOOT_TIMEOUT_S:.0f}s",
                ) from exc
            log.info("[startup] gateway: core connected — link established")
        else:
            local_client = LocalTurnClient(cast(IngressHandler, _handle_ingress))
            local_client.register_channel(adapter.channel_name, cli_pump, adapter)
            turn_client = local_client

        def _register_turn_channel(
            channel_name: str, pump: object, channel_adapter: object
        ) -> None:
            """Bind a started channel to the turn client, role-appropriately.

            mono/core use the in-process LocalTurnClient (pump-backed clarify);
            the GATEWAY just needs the adapter in the link's map so the core's
            output for that channel routes back over the socket (the core owns
            scan/clarify/consent).
            """
            if self._role == "gateway" and gateway_link is not None:
                gateway_link.register_adapter(channel_name, channel_adapter)  # type: ignore[arg-type]
            else:
                cast(LocalTurnClient, turn_client).register_channel(
                    channel_name, pump, channel_adapter
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
                        await turn_client.submit(msg)
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

        async def _core_frame_loop() -> None:
            """CORE driver: read inbound IngressFrames and submit each turn.

            Replaces the TUI receive loop on the core side. The gateway forwards
            every inbound message as an IngressFrame; for each, we lazily bind a
            SocketChannelAdapter + ClarifyPump for its originating channel (so
            acks/clarify route home, and the per-turn answer streams over the
            SocketStreamRegistry keyed by trace_id) and submit it through the same
            LocalTurnClient body the mono path uses. Ends when the gateway hangs
            up (clean EOF) — that drives the core's graceful teardown.
            """
            assert core_conn is not None
            core_client = cast(LocalTurnClient, turn_client)
            registered: set[str] = {adapter.channel_name}
            core_adapters: dict[str, SocketChannelAdapter] = {
                adapter.channel_name: cast(SocketChannelAdapter, adapter)
            }
            log.info("[startup] core: frame loop started")
            try:
                async for frame in core_conn:
                    if isinstance(frame, ConsentResponseFrame):
                        # The gateway's user answered a consent prompt — resolve
                        # the parked SocketConsentPrompter future so the tool runs
                        # (or is denied).
                        if socket_consent_prompter is not None:
                            socket_consent_prompter.resolve(frame.consent_id, frame.scope)
                        continue
                    if isinstance(frame, ClarifyReplyFrame):
                        # A tapped clarify button on the gateway — resolve the
                        # parked turn by clarify_id (parallel to the typed-reply
                        # path that arrives as a normal IngressFrame).
                        with contextlib.suppress(Exception):
                            clarify_gateway.try_resolve_by_id(
                                frame.clarify_id, frame.answer
                            )
                        continue
                    if not isinstance(frame, IngressFrame):
                        log.debug(
                            "[startup] core: ignoring non-ingress frame",
                            extra={"_fields": {"type": getattr(frame, "type", "?")}},
                        )
                        continue
                    msg = frame_to_ingress(frame)
                    if msg.channel not in registered:
                        chan_adapter = SocketChannelAdapter(
                            core_conn, channel_name=msg.channel
                        )
                        clarify_gateway.register_adapter(msg.channel, chan_adapter)
                        chan_pump = ClarifyPump(
                            clarify_gateway, stream_registry, clarify_classifier
                        )
                        core_client.register_channel(
                            msg.channel, chan_pump, chan_adapter
                        )
                        core_adapters[msg.channel] = chan_adapter
                        # Publish into the core's ChannelRegistry too, so the
                        # notification deliverer (proactive send_text + the
                        # send_file/send_message tools) can resolve this channel.
                        # Without it those fail "unknown channel"/"channel
                        # unavailable" because the real adapter lives in the
                        # gateway. Idempotent — guarded by `registered`.
                        from stackowl.channels.registry import ChannelRegistry

                        with contextlib.suppress(Exception):
                            ChannelRegistry.instance().register(chan_adapter)
                        registered.add(msg.channel)
                    try:
                        await turn_client.submit(msg)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 — top-level loop guard
                        log.error(
                            "[startup] core: turn submission failed — continuing",
                            exc_info=exc,
                            extra={"_fields": {"session_id": msg.session_id}},
                        )
                        with contextlib.suppress(Exception):
                            stream_registry.remove(msg.trace_id)
            except asyncio.CancelledError:
                log.info("[startup] core: frame loop cancelled")
                raise
            finally:
                log.info("[startup] core: frame loop ended (gateway disconnected)")

        # 3. STEP — start Telegram adapter if configured
        from stackowl.config.secret_resolver import SecretResolver

        telegram_adapter = None
        telegram_loop_task = None
        tg_cfg = self._settings.telegram_channel
        # v1 split scope is CLI/TUI only — extra channels stay on the mono path
        # (a split gateway forwards just the local TUI; Telegram/Slack/etc. land
        # in a later phase). mono is byte-identical.
        if self._role != "core" and tg_cfg.bot_token:
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
                from stackowl.channels.liveness import ChannelLivenessStore
                from stackowl.channels.telegram.adapter import TelegramChannelAdapter
                from stackowl.infra.clock import WallClock

                resolved_tg_settings = tg_cfg.model_copy(
                    update={"bot_token": resolved_token, "webhook_secret": resolved_webhook_secret}
                )
                telegram_adapter = TelegramChannelAdapter(
                    resolved_tg_settings,
                    progress=self._settings.progress if self._settings else None,
                    # PB0b/RC0 — gateway writes cross-process receive-liveness into
                    # the already-open shared pool; core's health sweep reads it.
                    liveness=ChannelLivenessStore(db_pool, WallClock()),
                )

                # E0-S1 — wire the Telegram consent round-trip BEFORE start() so a
                # message arriving at boot can never miss its prompter (would else
                # fail closed with a spurious denial). The prompter only needs the
                # adapter object; the callback handler is attached after start()
                # (it needs the live bot application).
                from stackowl.channels.telegram.consent import TelegramConsentPrompter

                tg_consent_prompter = TelegramConsentPrompter(telegram_adapter)
                consent_routing.register("telegram", tg_consent_prompter)

                # Voice transcription (opt-in, transcription.enabled): build the
                # local-first STT selector + voice handler BEFORE start() so the
                # filters.VOICE handler is registered inside start() (same reason
                # the consent prompter is wired pre-start). The vtx: confirm
                # callback is registered after start() on the shared callback
                # router. When disabled this whole block is skipped → byte-identical.
                tg_voice_pending: PendingTranscriptStore | None = None
                if self._settings and self._settings.transcription.enabled:
                    from stackowl.channels.telegram.voice import TelegramVoiceHandler
                    from stackowl.channels.telegram.voice_confirm import (
                        PendingTranscriptStore,
                    )
                    from stackowl.media.stt.selector import SttSelector

                    tg_voice_pending = PendingTranscriptStore()
                    tg_voice_selector = SttSelector(self._settings.transcription)
                    telegram_adapter.set_voice_handler(
                        TelegramVoiceHandler(
                            tg_voice_selector, telegram_adapter, tg_voice_pending
                        )
                    )
                    log.info("[startup] gateway: Telegram voice transcription enabled")
                    # Telegram voice notes are OGG/Opus → need ffmpeg to decode.
                    # Warn loudly at boot so a missing codec is diagnosable here,
                    # not only when the first voice note fails.
                    import shutil as _shutil

                    if _shutil.which("ffmpeg") is None:
                        log.warning(
                            "[startup] gateway: ffmpeg NOT found — Telegram voice "
                            "notes (OGG) cannot be transcribed until it is installed "
                            "(sudo apt install ffmpeg). TUI WAV dictation is unaffected."
                        )

                await telegram_adapter.start()
                # E5 — let the clarify gateway deliver questions over Telegram,
                # and give the Telegram loop its own clarify-aware dispatch pump.
                clarify_gateway.register_adapter("telegram", telegram_adapter)
                tg_pump = ClarifyPump(clarify_gateway, stream_registry, clarify_classifier)
                _register_turn_channel(
                    telegram_adapter.channel_name, tg_pump, telegram_adapter
                )

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
                    # Voice-transcript Send/Discard taps (only when transcription is
                    # enabled — tg_voice_pending is None otherwise).
                    if tg_voice_pending is not None:
                        from stackowl.channels.telegram.voice_confirm import (
                            CALLBACK_PREFIX,
                            VoiceConfirmHandler,
                        )

                        tg_voice_confirm = VoiceConfirmHandler(
                            telegram_adapter, tg_voice_pending
                        )
                        tg_callback_router.register(
                            f"{CALLBACK_PREFIX}:", tg_voice_confirm.handle_callback
                        )
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
                                await turn_client.submit(msg)
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
        if self._role != "core" and slack_cfg.bot_token and slack_cfg.app_token:
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
                _register_turn_channel(
                    slack_adapter.channel_name, slack_pump, slack_adapter
                )

                # Open the Socket Mode connection as a BACKGROUND task — boot must
                # never block on the live WebSocket handshake.
                slack_socket_handler = AsyncSocketModeHandler(app, resolved_app_token)
                slack_socket_task = asyncio.create_task(
                    slack_socket_handler.start_async()  # type: ignore[no-untyped-call]
                )

                # ADR-6 Task 6 fix — the real reconnect capability for
                # `SlackChannelAdapter.ensure_available()`. Tears down the
                # CURRENT socket-mode handler/task and builds a fresh one,
                # reusing `app` (every `@app.event`/`@app.action`/`@app.command`
                # handler registered above stays wired — only the socket
                # connection itself is rebuilt, never re-registered). `nonlocal`
                # rebinds the SAME `slack_socket_handler`/`slack_socket_task`
                # locals the shutdown block (below) reads, so a reconnect
                # mid-run leaves no stale reference for shutdown to miss.
                async def _slack_reconnect() -> None:
                    nonlocal slack_socket_handler, slack_socket_task
                    # 1. ENTRY
                    log.info(
                        "[startup] gateway: slack reconnect: entry",
                        extra={"_fields": {"had_handler": slack_socket_handler is not None}},
                    )
                    old_handler = slack_socket_handler
                    old_task = slack_socket_task
                    # 2. DECISION / 3. STEP — best-effort close the OLD handler +
                    # cancel its task BEFORE building the replacement, so a stale
                    # connection is never left running alongside a new one.
                    if old_handler is not None:
                        try:
                            await old_handler.close_async()  # type: ignore[no-untyped-call]
                        except Exception as exc:  # noqa: BLE001 — best-effort teardown
                            log.warning(
                                "[startup] gateway: slack reconnect: old handler close failed",
                                exc_info=exc,
                            )
                    if old_task is not None:
                        old_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await old_task
                    slack_socket_handler = AsyncSocketModeHandler(app, resolved_app_token)
                    slack_socket_task = asyncio.create_task(
                        slack_socket_handler.start_async()  # type: ignore[no-untyped-call]
                    )
                    # 4. EXIT
                    log.info(
                        "[startup] gateway: slack reconnect: exit — fresh socket task spawned"
                    )

                slack_adapter.set_reconnector(_slack_reconnect)

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
                                await turn_client.submit(msg)
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
        if self._role != "core" and discord_cfg.enabled and discord_cfg.bot_token:
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
                _register_turn_channel(
                    discord_adapter.channel_name, discord_pump, discord_adapter
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
                                await turn_client.submit(msg)
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
        if self._role != "core" and whatsapp_cfg.enabled:
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
                _register_turn_channel(
                    whatsapp_adapter.channel_name, whatsapp_pump, whatsapp_adapter
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
                                # it would be mistaken for a fresh turn. This pre-check
                                # is WhatsApp-specific, so it stays in the loop; the
                                # scan/resolve/intake tail goes through the shared seam
                                # (which re-scans — scanner.scan is pure/idempotent).
                                if await whatsapp_consent_prompter.resolve_reply(
                                    msg.session_id, input_text
                                ):
                                    continue
                                await turn_client.submit(msg)
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
        # CORE's driver is the inbound-frame loop (no TUI to receive from);
        # mono/gateway drive the channel-receive loop. Both submit via turn_client.
        loop_task = asyncio.create_task(
            _core_frame_loop() if self._role == "core" else _message_loop()
        )
        # Recover the scheduler's durable state from the prior run BEFORE the poll
        # loop starts: reap jobs left 'running' by a crash and replay/realarm overdue
        # ones, so an assigned task survives a restart instead of wedging forever.
        # Fail-open: a recovery error must NOT block startup — the scheduler still runs.
        # NOTE: a replay_missed=True job dispatches its handler INLINE here (before the
        # watchdog notify below), so keep such handlers light or background them if
        # replay handlers ever become heavy — else they delay startup readiness.
        # GATEWAY does not own the scheduler — the core recovers + runs it, so the
        # gateway must NOT recover (it would race the core reaping the same rows).
        if self._role != "gateway":
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

            # ADR-B / S9 — project every `lifecycle="scheduled"` owl into its owned
            # scheduler row (manifest = truth; rows = derived projection). Idempotent:
            # creates missing rows, deletes rows for gone/on-demand/retired owls,
            # never touches hand-made cronjobs. Runs in the SAME core-owns-scheduler
            # block as recover() so the gateway never races it. Fail-open: a reconcile
            # error must not block startup.
            try:
                from stackowl.scheduler.owl_lifecycle import reconcile_owl_schedules

                reconcile = await reconcile_owl_schedules(
                    owl_registry,
                    db_pool,
                    tz=self._settings.system.timezone or "UTC",
                    settings=self._settings,
                )
                log.info(
                    "[startup] gateway: owl schedules reconciled",
                    extra={"_fields": {
                        "created": reconcile.created, "updated": reconcile.updated,
                        "deleted": reconcile.deleted, "skipped": reconcile.skipped,
                    }},
                )
            except Exception as exc:
                log.error(
                    "[startup] gateway: owl schedule reconcile failed — starting anyway",
                    exc_info=exc,
                    extra={"_fields": {}},
                )

        # WS-E — STARTUP WIRING-CLOSURE audit. Runs AFTER recover() so seeded rows
        # exist, then warns loudly (never fails startup) when a registered "seeded"
        # handler has no standing jobs row (it would never fire) or a subscribed
        # bus event has no declared publisher. Advisory guard against the class of
        # "dangling half-edge" bug (registered-but-unreachable) that shipped green
        # for check_in / event_bridge / goal_execution.
        #
        # DECLARED_EVENT_PUBLISHERS — the set of bus events some module actually
        # EMITS. It is empty today: event_bridge._ALLOWED_EVENTS is empty (WS-D
        # moved proactivity onto the durable seam). Re-adding a bridge subscriber
        # (an event in _ALLOWED_EVENTS) REQUIRES adding its publisher name here,
        # or the audit will (correctly) flag it as a dangling subscription.
        try:
            from stackowl.notifications.event_bridge import _ALLOWED_EVENTS
            from stackowl.scheduler.base import HandlerRegistry
            from stackowl.startup.wiring_audit import audit_scheduler_wiring

            declared_event_publishers: frozenset[str] = frozenset()
            wiring_report = await audit_scheduler_wiring(
                db_pool,
                HandlerRegistry.instance(),
                allowed_events=_ALLOWED_EVENTS,
                declared_publishers=declared_event_publishers,
            )
            log.info(
                "[startup] gateway: scheduler wiring audited",
                extra={"_fields": {
                    "dangling_handlers": wiring_report.dangling_handlers,
                    "dangling_events": wiring_report.dangling_events,
                    "total_handlers": wiring_report.total_handlers,
                }},
            )
        except Exception as exc:
            # The audit is advisory + already no-raise; this is belt-and-braces so
            # an unexpected import/wiring error can NEVER block startup.
            log.error(
                "[startup] gateway: wiring audit failed — starting anyway",
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
        # GATEWAY does not run turns, so it owns no durable tasks to recover — the
        # core reconstructs and re-drives them.
        if self._role != "gateway":
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
        # morning brief, etc.) actually dispatch. GATEWAY skips this — the core
        # owns the scheduler loop, so running it here would double every job.
        scheduler_task: asyncio.Task[None] | None = None
        if self._role != "gateway":
            scheduler_task = asyncio.create_task(
                scheduler_components.supervisor.start()
            )

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

        # CORE restart triggers (Phase 3/4). SIGHUP is the manual trigger (e.g.
        # `kill -HUP <core_pid>`) to validate the drain/execv path; CodeWatcher is
        # the automatic one. Both just set restart_event — the driver above turns
        # that into quiesce -> teardown -> os.execv. Gateway/mono never arm these.
        if self._role == "core":
            def _request_restart() -> None:
                if not restart_event.is_set():
                    log.info("[startup] core: restart trigger received")
                    restart_event.set()

            if hasattr(signal, "SIGHUP"):
                with contextlib.suppress(NotImplementedError):
                    loop.add_signal_handler(signal.SIGHUP, _request_restart)

            auto = self._settings.runtime.auto_restart
            if auto.enabled:
                # delay_minutes is the quiet-period settle; require_client_connected
                # is satisfied by construction here (the core only runs while its
                # gateway connection is live). A burst of edits coalesces into one
                # restart via the watcher's debounce. on_change fires on the watcher
                # thread → marshal onto the loop.
                def _on_code_change() -> None:
                    loop.call_soon_threadsafe(_request_restart)

                code_watcher = CodeWatcher(
                    watch_paths=[Path(p) for p in auto.watch_paths],
                    on_change=_on_code_change,
                    poll_interval_s=auto.poll_interval_s,
                    quiet_period_s=auto.delay_minutes * 60.0,
                )
                code_watcher.capture_loop(loop)
                code_watcher.start()
                log.info(
                    "[startup] core: code watcher armed",
                    extra={"_fields": {
                        "watch_paths": auto.watch_paths,
                        "delay_minutes": auto.delay_minutes,
                    }},
                )

        # F142 — start the REAL recurring systemd watchdog (self-skips off-systemd).
        # F-85 — gate the ping on a REAL liveness signal so a wedged-but-spinning
        # loop can no longer keep telling systemd "healthy". We probe only the
        # LOCAL critical subsystems (db pool + data/log dirs) — a wedged db or an
        # unwritable data dir means the process genuinely cannot work, so the ping
        # is skipped and systemd's watchdog-timeout restarts the unit. Network
        # provider health is deliberately EXCLUDED: a provider outage is not a
        # reason to kill this process. is_live() fails OPEN on probe error.
        watchdog = WatchdogService()
        liveness_aggregator = _build_liveness_aggregator()
        watchdog.start(liveness_check=liveness_aggregator.is_live)
        # READY=1 ONCE, AFTER all assembly is done and we are about to serve — never
        # earlier (premature READY would let systemd start dependents while startup
        # could still fail). No-op off-systemd.
        watchdog.send_ready()
        # GATEWAY routes the core's outbound frames back to the adapters via
        # GatewayLink.run(conn) — driven by the IpcServer accept handler (one per
        # core connection), so a core exec-replace is handled by re-accepting. The
        # gateway just runs the TUI here; the link lives in the accept tasks. The
        # core supervisor (crash-respawn) runs alongside.
        supervise_task: asyncio.Task[None] | None = None
        if self._role == "gateway":
            # F-39 — deliver a user-visible notice when the core crash-respawns, so a
            # self-heal is not silent. Reuses the proactive deliverer; None when no
            # deliverer is wired (the respawn then notifies via its LOUD log only).
            _crash_brief_channels = list(self._settings.brief.channels)

            async def _notify_core_crash(rc: int | None) -> None:
                if proactive_deliverer is None:
                    return
                from stackowl.notifications.router import Notification

                brief_channels = _crash_brief_channels
                await proactive_deliverer.deliver(Notification(
                    message=(
                        "⚠ StackOwl restarted after an unexpected core exit "
                        f"(rc={rc}). Recovering automatically — in-flight work may "
                        "need to be re-sent."
                    ),
                    urgency="critical",
                    category="operator_health",
                    channel_name=brief_channels[0] if brief_channels else None,
                ))

            supervise_task = asyncio.create_task(
                _supervise_core(
                    core_proc_holder,
                    gateway_socket_path,
                    stop_event,
                    gateway_first_conn_ready,
                    on_crash=_notify_core_crash,
                )
            )
        try:
            if self._role == "core":
                # No TUI to run — the inbound-frame loop is the blocking driver.
                # Race it against a stop signal AND the restart trigger (code change
                # or SIGHUP). On restart we drain in-flight turns, then fall through
                # to teardown and os.execv (fresh interpreter = new code).
                stop_task = asyncio.ensure_future(stop_event.wait())
                restart_task = asyncio.ensure_future(restart_event.wait())
                try:
                    await asyncio.wait(
                        {loop_task, stop_task, restart_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if restart_event.is_set() and not stop_event.is_set():
                        assert core_conn is not None
                        log.info("[startup] core: restart requested — quiescing")
                        # Tell the gateway to buffer before we stop accepting.
                        with contextlib.suppress(Exception):
                            await core_conn.send(
                                RestartNoticeFrame(
                                    reason="code-change",
                                    grace_seconds=_restart_grace,
                                )
                            )
                        # Stop accepting new turns (the frame loop), then drain the
                        # turns already running.
                        loop_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await loop_task
                        await quiesce(turn_registry, grace_seconds=_restart_grace)
                        restart_requested["value"] = True
                finally:
                    stop_task.cancel()
                    restart_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await stop_task
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await restart_task
            else:
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
            # CORE auto-restart watcher (None unless this is the core w/ auto_restart).
            if code_watcher is not None:
                with contextlib.suppress(Exception):
                    code_watcher.stop()
            # GATEWAY crash-respawn supervisor (None in mono/core).
            if supervise_task is not None:
                supervise_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await supervise_task
            # GATEWAY tear-down of the split socket: stop the listener, then the
            # core child. On a normal shutdown the core gets a terminate; a
            # code-change restart never reaches here (the core execs itself).
            if gateway_server is not None:
                with contextlib.suppress(Exception):
                    await gateway_server.stop()
            _gw_proc = core_proc_holder.get("proc")
            if _gw_proc is not None and _gw_proc.returncode is None:
                with contextlib.suppress(Exception):
                    _gw_proc.terminate()
            # CORE's connection to the gateway (None in mono/gateway).
            if core_conn is not None:
                with contextlib.suppress(Exception):
                    await core_conn.aclose()
            # scheduler_task is None on the GATEWAY (it skips the scheduler loop).
            if scheduler_task is not None:
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
        # CORE code-change restart: teardown has released the DB pool / browser /
        # sockets, so exec-replace this process with a fresh interpreter (= new
        # code). The same PID re-runs `__core__`, reconnects to the durable
        # gateway, and the gateway flushes anything buffered during the gap. execv
        # never returns; nothing below runs.
        if self._role == "core" and restart_requested["value"]:
            log.info("[startup] core: exec-replacing with fresh code (restart)")
            with contextlib.suppress(Exception):
                logging.shutdown()
            os.execv(sys.executable, [sys.executable, "-m", "stackowl", "__core__"])
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
