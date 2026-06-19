"""Command assembler — single entry-point for all slash-command registration.

This module owns ONE call site for every slash command.  The startup
orchestrator builds a :class:`CommandDeps` from live objects and calls
:func:`register_all_commands` once.  Nothing else calls
``create_and_register`` or ``load_builtin_commands`` directly.

Pattern A (dependency-free) commands self-register via a module-level
``_CMD = register_command(Cmd())`` at the bottom of their ``*_command.py``
file; :func:`load_builtin_commands` triggers those imports.

Pattern B (DI) commands are constructed here with their live dependencies
and registered explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only; no runtime cost
    from collections.abc import Callable
    from pathlib import Path

    from stackowl.commands.base import SlashCommand
    from stackowl.commands.registry import CommandRegistry
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.events.bus import EventBus
    from stackowl.integrations.registry import IntegrationRegistry
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.memory.fact_promoter import FactPromoter
    from stackowl.memory.lancedb_adapter import LanceDBAdapter
    from stackowl.notifications.router import NotificationRouter
    from stackowl.owls.registry import OwlRegistry
    from stackowl.parliament.orchestrator import ParliamentOrchestrator
    from stackowl.plugins.registry import PluginRegistry
    from stackowl.scheduler.scheduler import JobScheduler
    from stackowl.skills.loader import SkillLoader
    from stackowl.skills.store import SkillIndexStore
    from stackowl.tools.registry import ToolRegistry


@dataclass
class CommandDeps:
    """All dependencies any slash command might need.

    Every field is Optional (defaults None) so a partial set of deps can
    be passed without error — commands emit their own "not configured"
    message at runtime when a required dep is absent.
    """

    # Core infrastructure
    event_bus: EventBus | None = None
    db: DbPool | None = None
    router: NotificationRouter | None = None
    settings: Settings | None = None

    # Registries
    owl_registry: OwlRegistry | None = None
    tool_registry: ToolRegistry | None = None
    plugin_registry: PluginRegistry | None = None
    integration_registry: IntegrationRegistry | None = None

    # Memory subsystem
    bridge: MemoryBridge | None = None
    preference_store: object | None = None  # PreferenceStore — avoid heavy import
    lancedb: LanceDBAdapter | None = None
    promoter: FactPromoter | None = None
    embedding_registry: EmbeddingRegistry | None = None

    # Skills subsystem
    skills_store: SkillIndexStore | None = None
    skills_loader: SkillLoader | None = None
    skills_root: Path | None = None

    # Audit / observability
    audit_logger: object | None = None  # AuditLogger — avoid heavy import

    # Scheduler
    scheduler: object | None = None  # SchedulerRegistry — avoid heavy import

    # Parliament / agents
    parliament_orchestrator: ParliamentOrchestrator | None = None
    morning_brief_handler: object | None = None  # MorningBriefHandler

    # Provider registry (for /agent create)
    provider_registry: object | None = None  # ProviderRegistry — avoid heavy import

    # Parliament session store (for /parliament log)
    parliament_session_store: object | None = None  # SessionStore — avoid heavy import



def register_all_commands(
    deps: CommandDeps,
    registry: CommandRegistry | None = None,
) -> CommandRegistry:
    """Register every shipped slash command onto *registry*.

    Parameters
    ----------
    deps:
        All live dependencies.  Missing deps (None) are tolerated — each
        command emits a "not configured" message at dispatch time.
    registry:
        Defaults to ``CommandRegistry.instance()``.  Pass an explicit
        instance (e.g. a fresh one from ``CommandRegistry()`` after
        ``CommandRegistry.reset()``) in tests.

    Returns
    -------
    CommandRegistry
        The registry after all commands have been registered.
    """
    from stackowl.commands.registry import CommandRegistry, load_builtin_commands

    reg = registry if registry is not None else CommandRegistry.instance()

    # ── 1. Pattern-A commands (dependency-free, self-register on import) ───
    # load_builtin_commands() imports every *_command.py module and also
    # re-registers any already-cached _CMD instances (handles post-reset()
    # scenarios where importlib.import_module is a no-op).
    # Pass *reg* so Pattern-A commands land in the same target registry as
    # Pattern-B DI commands — not necessarily the global singleton.
    load_builtin_commands(registry=reg)

    # ── 2. Pattern-B commands (DI, constructed + registered here) ──────────
    _register_di_commands(deps, reg)

    registered = [c.command for c in reg.list()]
    log.gateway.info(
        "[commands] assembly.register_all_commands: exit",
        extra={"_fields": {"count": len(registered), "commands": sorted(registered)}},
    )
    return reg


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_register(
    registry: CommandRegistry,
    label: str,
    factory: Callable[[], SlashCommand],
) -> None:
    """Construct + register ONE command, isolating its failure from the rest.

    Registration is dep-INDEPENDENT (factories are called even with None deps),
    so the reachability guard (run with empty deps) is a true proxy for
    production reachability. But a future command whose ``__init__`` does eager
    I/O could raise at construction; without isolation that single failure would
    abort registration of every command after it — silently vanishing a whole
    swath of the surface into "Unknown slash command" (the exact
    "looks-wired-but-never-fires" bug this overhaul exists to kill). So each
    command is constructed in its own try/except: a failure is logged + skipped,
    the others still register, and the ``== SHIPPED_COMMANDS`` guard flags the
    one that went missing.
    """
    try:
        registry.register(factory())
    except Exception as exc:  # noqa: BLE001 — one bad command must not abort the rest
        log.gateway.error(
            "[commands] assembly: command failed to construct/register — skipped",
            exc_info=exc,
            extra={"_fields": {"command": label}},
        )


def _register_di_commands(deps: CommandDeps, registry: CommandRegistry) -> None:
    """Construct and register each DI command using deps.

    Registration is UNCONDITIONAL — a command is registered even when one of
    its deps is None.  This is deliberate: it makes "shipped ⟺ registered" an
    invariant that does NOT depend on runtime wiring, so the reachability guard
    (which runs with empty deps) is a true proxy for production reachability.
    If the orchestrator ever forgets to populate a dep, the command still
    registers and emits an honest "not configured" message at dispatch time —
    rather than silently vanishing into "Unknown slash command" (the exact
    "looks-wired-but-never-fires" bug this whole overhaul exists to kill).
    All command ``__init__`` methods tolerate None deps (verified). Each
    construction is isolated via :func:`_safe_register`.
    """

    # /skill
    from stackowl.commands.skill_command import SkillCommand
    _safe_register(registry, "skill", lambda: SkillCommand(
        store=deps.skills_store,
        loader=deps.skills_loader,
        skills_root=deps.skills_root,
        embedding_registry=deps.embedding_registry,
    ))

    # /memory
    from stackowl.commands.memory_command import MemoryCommand
    _safe_register(registry, "memory", lambda: MemoryCommand(
        bridge=deps.bridge,
        settings=deps.settings,
        db=deps.db,
        event_bus=deps.event_bus,
        lancedb=deps.lancedb,
        promoter=deps.promoter,
        embedding_registry=deps.embedding_registry,
    ))

    # /owls
    from stackowl.commands.owls_command import OwlsCommand
    _safe_register(registry, "owls", lambda: OwlsCommand(
        owl_registry=deps.owl_registry,
        db=deps.db,
        event_bus=deps.event_bus,
        tool_registry=deps.tool_registry,
    ))

    # /focus
    from stackowl.commands.focus_command import FocusCommand
    _safe_register(registry, "focus", lambda: FocusCommand(router=deps.router, event_bus=deps.event_bus))

    # /urgent
    from stackowl.commands.urgent_command import UrgentCommand
    _safe_register(registry, "urgent", lambda: UrgentCommand(router=deps.router))

    # /quiet
    from stackowl.commands.quiet_command import QuietHoursCommand
    _safe_register(registry, "quiet", lambda: QuietHoursCommand(db=deps.db))

    # /notifications
    from stackowl.commands.notifications_command import NotificationsMissedCommand
    _safe_register(registry, "notifications", lambda: NotificationsMissedCommand(db=deps.db))

    # /why
    from stackowl.commands.why import WhyCommand
    _safe_register(registry, "why", lambda: WhyCommand())

    # /whoami
    from stackowl.commands.whoami import WhoamiCommand
    _safe_register(registry, "whoami", lambda: WhoamiCommand(owl_registry=deps.owl_registry))

    # /audit (and /audit export subcommand)
    from stackowl.audit.logger import AuditLogger
    from stackowl.commands.audit import AuditCommand
    _export_key = deps.settings.governance.audit_export_key if deps.settings is not None else ""
    _safe_register(registry, "audit", lambda: AuditCommand(
        audit_logger=cast("AuditLogger | None", deps.audit_logger),
        export_key=_export_key,
    ))

    # /brief
    from stackowl.commands.brief_command import BriefCommand
    from stackowl.scheduler.handlers.morning_brief import MorningBriefHandler
    _safe_register(registry, "brief", lambda: BriefCommand(
        handler=cast("MorningBriefHandler | None", deps.morning_brief_handler)))

    # /webhook
    from stackowl.commands.webhook_command import WebhookCommand
    _safe_register(registry, "webhook", lambda: WebhookCommand(db=deps.db, settings=deps.settings))

    # /permissions
    from stackowl.commands.permissions import PermissionsCommand
    _safe_register(registry, "permissions", lambda: PermissionsCommand(
        settings=deps.settings,
        integration_registry=deps.integration_registry,
        plugin_registry=deps.plugin_registry,
    ))

    # /agent — unified create (create/confirm/cancel) + manage
    # (list/log/pause/resume/stop/acknowledge). Replaces the old split
    # /agent + /agents surfaces.
    from stackowl.commands.agent_create_command import AgentCommand
    from stackowl.providers.registry import ProviderRegistry
    _safe_register(registry, "agent", lambda: AgentCommand(
        scheduler=cast("JobScheduler | None", deps.scheduler),
        provider_registry=cast("ProviderRegistry | None", deps.provider_registry),
        db=deps.db,
        event_bus=deps.event_bus,
    ))

    # /parliament
    from stackowl.commands.parliament_command import ParliamentCommand
    from stackowl.parliament.session_store import SessionStore
    _safe_register(registry, "parliament", lambda: ParliamentCommand(
        orchestrator=deps.parliament_orchestrator,
        session_store=cast("SessionStore | None", deps.parliament_session_store),
        owl_registry=deps.owl_registry,
        event_bus=deps.event_bus,
    ))

    # /reset
    from stackowl.commands.reset import ResetCommand
    _safe_register(registry, "reset", lambda: ResetCommand(bridge=deps.bridge))

    # /connect + /disconnect
    from stackowl.commands.connect_command import ConnectCommand, DisconnectCommand
    _safe_register(registry, "connect", lambda: ConnectCommand(integration_registry=deps.integration_registry))
    _safe_register(registry, "disconnect", lambda: DisconnectCommand(integration_registry=deps.integration_registry))

    # /plugins
    from stackowl.commands.plugins_command import PluginsCommand
    _safe_register(registry, "plugins", lambda: PluginsCommand(plugin_registry=deps.plugin_registry))

    # /staged
    from stackowl.commands.staged_command import StagedCommand
    _safe_register(registry, "staged", lambda: StagedCommand(
        bridge=deps.bridge,
        promoter=deps.promoter,
        event_bus=deps.event_bus,
    ))

    # /config — moved from Pattern-A to DI so the live event_bus is wired (C1)
    from stackowl.commands.config_command import ConfigCommand
    _safe_register(registry, "config", lambda: ConfigCommand(event_bus=deps.event_bus))

    # /settings — moved from Pattern-A to DI so the live event_bus is wired (C1)
    from stackowl.commands.settings_command import SettingsCommand
    _safe_register(registry, "settings", lambda: SettingsCommand(event_bus=deps.event_bus))

    # /provider — moved from Pattern-A to DI so the live event_bus is wired (C1)
    from stackowl.commands.provider_command import ProviderCommand
    _safe_register(registry, "provider", lambda: ProviderCommand(event_bus=deps.event_bus))
